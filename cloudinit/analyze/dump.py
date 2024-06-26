# This file is part of cloud-init. See LICENSE file for license information.

import calendar
import sys
from datetime import datetime, timezone

from cloudinit import atomic_helper, subp, util

stage_to_description = {
    "finished": "finished running cloud-init",
    "init-local": "starting search for local datasources",
    "init-network": "searching for network datasources",
    "init": "searching for network datasources",
    "modules-config": "running config modules",
    "modules-final": "finalizing modules",
    "modules": "running modules for",
    "single": "running single module ",
}

# logger's asctime format
CLOUD_INIT_ASCTIME_FMT = "%Y-%m-%d %H:%M:%S,%f"

# journctl -o short-precise
CLOUD_INIT_JOURNALCTL_FMT = "%b %d %H:%M:%S.%f %Y"

# other
DEFAULT_FMT = "%b %d %H:%M:%S %Y"


def parse_timestamp(timestampstr):
    # default syslog time does not include the current year
    months = [calendar.month_abbr[m] for m in range(1, 13)]
    if timestampstr.split()[0] in months:
        # Aug 29 22:55:26
        FMT = DEFAULT_FMT
        if "." in timestampstr:
            FMT = CLOUD_INIT_JOURNALCTL_FMT
        dt = datetime.strptime(
            timestampstr + " " + str(datetime.now().year),
            FMT,
        ).replace(tzinfo=timezone.utc)
        timestamp = dt.timestamp()
    elif "," in timestampstr:
        # 2016-09-12 14:39:20,839
        dt = datetime.strptime(timestampstr, CLOUD_INIT_ASCTIME_FMT).replace(
            tzinfo=timezone.utc
        )
        timestamp = dt.timestamp()
    else:
        # allow GNU date(1) to handle other formats we don't expect
        # This may throw a ValueError if no GNU date can be found
        timestamp = parse_timestamp_from_date(timestampstr)

    return float(timestamp)


def has_gnu_date():
    """GNU date includes a string containing the word GNU in it in
    help output. Posix date does not. Use this to indicate on Linux
    systems without GNU date that the extended parsing is not
    available.
    """
    return "GNU" in subp.subp(["date", "--help"]).stdout


def parse_timestamp_from_date(timestampstr):
    if not util.is_Linux() and subp.which("gdate"):
        date = "gdate"
    elif has_gnu_date():
        date = "date"
    else:
        raise ValueError(
            f"Unable to parse timestamp without GNU date: [{timestampstr}]"
        )
    return float(
        subp.subp([date, "-u", "+%s.%3N", "-d", timestampstr]).stdout.strip()
    )


def parse_ci_logline(line):
    # Stage Starts:
    # Cloud-init v. 0.7.7 running 'init-local' at \
    #               Fri, 02 Sep 2016 19:28:07 +0000. Up 1.0 seconds.
    # Cloud-init v. 0.7.7 running 'init' at \
    #               Fri, 02 Sep 2016 19:28:08 +0000. Up 2.0 seconds.
    # Cloud-init v. 0.7.7 finished at
    # Aug 29 22:55:26 test1 [CLOUDINIT] handlers.py[DEBUG]: \
    #               finish: modules-final: SUCCESS: running modules for final
    # 2016-08-30T21:53:25.972325+00:00 y1 [CLOUDINIT] handlers.py[DEBUG]: \
    #               finish: modules-final: SUCCESS: running modules for final
    #
    # Nov 03 06:51:06.074410 x2 cloud-init[106]: [CLOUDINIT] util.py[DEBUG]: \
    #               Cloud-init v. 0.7.8 running 'init-local' at \
    #               Thu, 03 Nov 2016 06:51:06 +0000. Up 1.0 seconds.
    #
    # 2017-05-22 18:02:01,088 - util.py[DEBUG]: Cloud-init v. 0.7.9 running \
    #         'init-local' at Mon, 22 May 2017 18:02:01 +0000. Up 2.0 seconds.
    #
    # Apr 30 19:39:11 cloud-init[2673]: handlers.py[DEBUG]: start: \
    #          init-local/check-cache: attempting to read from cache [check]

    amazon_linux_2_sep = " cloud-init["
    separators = [" - ", " [CLOUDINIT] ", amazon_linux_2_sep]
    found = False
    for sep in separators:
        if sep in line:
            found = True
            break

    if not found:
        return None

    (timehost, eventstr) = line.split(sep)

    # journalctl -o short-precise
    if timehost.endswith(":"):
        timehost = " ".join(timehost.split()[0:-1])

    if "," in timehost:
        timestampstr, extra = timehost.split(",")
        timestampstr += ",%s" % extra.split()[0]
        if " " in extra:
            hostname = extra.split()[-1]
    else:
        hostname = timehost.split()[-1]
        if sep == amazon_linux_2_sep:
            # This is an Amazon Linux style line, with no hostname and a PID.
            # Use the whole of timehost as timestampstr, and strip off the PID
            # from the start of eventstr.
            timestampstr = timehost.strip()
            eventstr = eventstr.split(maxsplit=1)[1]
        else:
            timestampstr = timehost.split(hostname)[0].strip()
    if "Cloud-init v." in eventstr:
        event_type = "start"
        if "running" in eventstr:
            stage_and_timestamp = eventstr.split("running")[1].lstrip()
            event_name, _ = stage_and_timestamp.split(" at ")
            event_name = event_name.replace("'", "").replace(":", "-")
            if event_name == "init":
                event_name = "init-network"
        else:
            # don't generate a start for the 'finished at' banner
            return None
        event_description = stage_to_description[event_name]
    else:
        (_pymodloglvl, event_type, event_name) = eventstr.split()[0:3]
        event_description = eventstr.split(event_name)[1].strip()

    event = {
        "name": event_name.rstrip(":"),
        "description": event_description,
        "timestamp": parse_timestamp(timestampstr),
        "origin": "cloudinit",
        "event_type": event_type.rstrip(":"),
    }
    if event["event_type"] == "finish":
        result = event_description.split(":")[0]
        desc = event_description.split(result)[1].lstrip(":").strip()
        event["result"] = result
        event["description"] = desc.strip()

    return event


def dump_events(cisource=None, rawdata=None):
    events = []
    event = None
    CI_EVENT_MATCHES = ["start:", "finish:", "Cloud-init v."]

    if not any([cisource, rawdata]):
        raise ValueError("Either cisource or rawdata parameters are required")

    if rawdata:
        data = rawdata.splitlines()
    else:
        data = cisource.readlines()

    for line in data:
        for match in CI_EVENT_MATCHES:
            if match in line:
                try:
                    event = parse_ci_logline(line)
                except ValueError:
                    sys.stderr.write("Skipping invalid entry\n")
                if event:
                    events.append(event)

    return events, data


def main():
    if len(sys.argv) > 1:
        cisource = open(sys.argv[1])
    else:
        cisource = sys.stdin

    return atomic_helper.json_dumps(dump_events(cisource))


if __name__ == "__main__":
    print(main())
