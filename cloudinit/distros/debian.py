# vi: ts=4 expandtab
#
#    Copyright (C) 2012 Canonical Ltd.
#    Copyright (C) 2012 Hewlett-Packard Development Company, L.P.
#    Copyright (C) 2012 Yahoo! Inc.
#
#    Author: Scott Moser <scott.moser@canonical.com>
#    Author: Juerg Haefliger <juerg.haefliger@hp.com>
#    Author: Joshua Harlow <harlowja@yahoo-inc.com>
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU General Public License version 3, as
#    published by the Free Software Foundation.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU General Public License for more details.
#
#    You should have received a copy of the GNU General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from cloudinit import distros
from cloudinit import helpers
from cloudinit import log as logging
from cloudinit import util

from cloudinit.distros.parsers.hostname import HostnameConf

from cloudinit.settings import PER_INSTANCE

LOG = logging.getLogger(__name__)


class Distro(distros.Distro):
    hostname_conf_fn = "/etc/hostname"
    locale_conf_fn = "/etc/default/locale"
    network_conf_fn = "/etc/network/interfaces"
    tz_conf_fn = "/etc/timezone"
    tz_local_fn = "/etc/localtime"
    tz_zone_dir = "/usr/share/zoneinfo"

    def __init__(self, name, cfg, paths):
        distros.Distro.__init__(self, name, cfg, paths)
        # This will be used to restrict certain
        # calls from repeatly happening (when they
        # should only happen say once per instance...)
        self._runner = helpers.Runners(paths)

    def apply_locale(self, locale, out_fn=None):
        if not out_fn:
            out_fn = self.locale_conf_fn
        util.subp(['locale-gen', locale], capture=False)
        util.subp(['update-locale', locale], capture=False)
        # "" provides trailing newline during join
        lines = [
            util.make_header(),
            'LANG="%s"' % (locale),
            "",
        ]
        util.write_file(out_fn, "\n".join(lines))

    def install_packages(self, pkglist):
        self.update_package_sources()
        self.package_command('install', pkglist)

    def _write_network(self, settings):
        util.write_file(self.network_conf_fn, settings)
        return ['all']

    def _bring_up_interfaces(self, device_names):
        use_all = False
        for d in device_names:
            if d == 'all':
                use_all = True
        if use_all:
            return distros.Distro._bring_up_interface(self, '--all')
        else:
            return distros.Distro._bring_up_interfaces(self, device_names)

    def set_hostname(self, hostname):
        self._write_hostname(hostname, self.hostname_conf_fn)
        self._apply_hostname(hostname)

    def _write_hostname(self, your_hostname, out_fn):
        conf = self._read_hostname_conf(out_fn)
        if not conf:
            conf = HostnameConf('')
            conf.parse()
        conf.set_hostname(your_hostname)
        util.write_file(out_fn, str(conf), 0644)

    def _read_system_hostname(self):
        conf = self._read_hostname_conf(self.hostname_conf_fn)
        if conf:
            sys_hostname = conf.hostname
        else:
            sys_hostname = None
        return (self.hostname_conf_fn, sys_hostname)

    def _read_hostname_conf(self, filename):
        try:
            conf = HostnameConf(util.load_file(filename))
            conf.parse()
            return conf
        except IOError:
            util.logexc(LOG, "Error reading hostname from %s", filename)
            return None

    def _read_hostname(self, filename, default=None):
        conf = self._read_hostname_conf(filename)
        if not conf:
            return default
        if not conf.hostname:
            return default
        return conf.hostname

    def _get_localhost_ip(self):
        # Note: http://www.leonardoborda.com/blog/127-0-1-1-ubuntu-debian/
        return "127.0.1.1"

    def set_timezone(self, tz):
        # TODO(harlowja): move this code into
        # the parent distro...
        tz_file = os.path.join(self.tz_zone_dir, str(tz))
        if not os.path.isfile(tz_file):
            raise RuntimeError(("Invalid timezone %s,"
                                " no file found at %s") % (tz, tz_file))
        # "" provides trailing newline during join
        tz_lines = [
            util.make_header(),
            str(tz), 
            "",
        ]
        util.write_file(self.tz_conf_fn, "\n".join(tz_lines))
        # This ensures that the correct tz will be used for the system
        util.copy(tz_file, self.tz_local_fn)

    def package_command(self, command, args=None):
        e = os.environ.copy()
        # See: http://tiny.cc/kg91fw
        # Or: http://tiny.cc/mh91fw
        e['DEBIAN_FRONTEND'] = 'noninteractive'
        cmd = ['apt-get', '--option', 'Dpkg::Options::=--force-confold',
               '--assume-yes', '--quiet', command]
        if args:
            cmd.extend(args)
        # Allow the output of this to flow outwards (ie not be captured)
        util.subp(cmd, env=e, capture=False)

    def update_package_sources(self):
        self._runner.run("update-sources", self.package_command,
                         ["update"], freq=PER_INSTANCE)

    def get_primary_arch(self):
        (arch, _err) = util.subp(['dpkg', '--print-architecture'])
        return str(arch).strip()
