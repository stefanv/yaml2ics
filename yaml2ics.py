"""
yaml2ics
========

CLI to convert yaml into ics.
"""
import datetime
import os
import sys

import dateutil
import dateutil.rrule
import ics
import yaml
from dateutil.tz import gettz

interval_type = {
    "seconds": dateutil.rrule.SECONDLY,
    "minutes": dateutil.rrule.MINUTELY,
    "hours": dateutil.rrule.HOURLY,
    "days": dateutil.rrule.DAILY,
    "weeks": dateutil.rrule.WEEKLY,
    "months": dateutil.rrule.MONTHLY,
    "years": dateutil.rrule.YEARLY,
}


def datetime2utc(date):
    if isinstance(date, datetime.datetime):
        return datetime.datetime.strftime(date, "%Y%m%dT%H%M%S")
    elif isinstance(date, datetime.date):
        return datetime.datetime.strftime(date, "%Y%m%d")


# See RFC2445, 4.8.5 REcurrence Component Properties
# This function can be used to add a list of e.g. exception dates (EXDATE) or recurrence dates (RDATE)
# to a reoccurring event
def add_recurrence_property(
    event: ics.Event, property_name, dates: map, tz: datetime.tzinfo = None
):
    if tz:
        event.extra.append(
            ics.ContentLine(
                name=property_name,
                params={"TZID": [str(ics.Timezone.from_tzinfo(tz))]},
                value=",".join(dates),
            )
        )
    else:
        event.extra.append(
            ics.ContentLine(
                name=property_name,
                value=",".join(dates),
            )
        )


def event_from_yaml(event_yaml: dict, tz: datetime.tzinfo = None) -> ics.Event:
    d = event_yaml
    repeat = d.pop("repeat", None)
    ics_custom = d.pop("ics", None)

    if "timezone" in d:
        tz = gettz(d.pop("timezone"))

    # Strip all string values, since they often end on `\n`
    for key in d:
        d[key] = d[key].strip() if isinstance(d[key], str) else d[key]

    # See https://icspy.readthedocs.io/en/stable/api.html#event
    #
    # Keywords:
    # name, begin, end, duration, uid, description, created, last_modified,
    # location, url, transparent, alarms, attendees, categories, status,
    # organizer, geo, classification
    event = ics.Event(**d)

    # Handle all-day events
    if not ("duration" in d or "end" in d):
        event.make_all_day()

    if repeat:
        interval = repeat["interval"]

        if not len(interval) == 1:
            print(
                "Error: interval must specify seconds, minutes, hours, days, weeks, months, or years only",
                file=sys.stderr,
            )
            sys.exit(-1)

        interval_measure = list(interval.keys())[0]
        if interval_measure not in interval_type:
            print(
                "Error: expected interval to be specified in seconds, minutes, hours, days, weeks, months, or years only",
                file=sys.stderr,
            )
            sys.exit(-1)

        if "until" not in repeat:
            print("Error: must specify end date for repeating events", file=sys.stderr)
            sys.exit(-1)

        # This causes zero-length events, I guess overriding whatever duration might have been specified.
        # event.end = d.get('end', None)

        rrule = dateutil.rrule.rrule(
            freq=interval_type[interval_measure],
            interval=interval[interval_measure],
            until=repeat.get("until"),
            dtstart=d["begin"],
        )

        rrule_lines = str(rrule).split("\n")
        rrule_dtstart = [line for line in rrule_lines if line.startswith("DTSTART")][0]
        rrule_dtstart = rrule_dtstart + "Z"
        rrule_rrule = [line for line in rrule_lines if line.startswith("RRULE")][0]
        event.extra.append(
            ics.ContentLine(
                name=rrule_rrule.split(":", 1)[0],
                value=rrule_rrule.split(":", 1)[1],
            )
        )

        if "except_on" in repeat:
            exdates = map(
                lambda exdate: datetime2utc(exdate),
                repeat["except_on"],
            )
            add_recurrence_property(event, "EXDATE", exdates, tz)

        if "also_on" in repeat:
            rdates = map(
                lambda rdate: datetime2utc(rdate),
                repeat["also_on"],
            )
            add_recurrence_property(event, "RDATE", rdates, tz)

    event.dtstamp = datetime.datetime.utcnow().replace(tzinfo=dateutil.tz.UTC)
    if tz and event.floating and not event.all_day:
        event.replace_timezone(tz)

    if ics_custom:
        for line in ics_custom.split("\n"):
            if ":" not in line:
                raise RuntimeError(
                    f"Invalid custom ICS (expected `fieldname:value`):\n  {line}"
                )

            ruletype, content = line.split(":", maxsplit=1)
            event.extra.append(ics.ContentLine(name=ruletype, value=content))

    return event


def events_to_calendar(events: list) -> str:
    cal = ics.Calendar()
    for event in events:
        cal.events.append(event)
    return cal


def files_to_events(files: list) -> (ics.Calendar, str):
    """Process files to a list of events"""
    all_events = []
    name = None

    for f in files:
        if hasattr(f, "read"):
            calendar_yaml = yaml.load(f.read(), Loader=yaml.FullLoader)
        else:
            calendar_yaml = yaml.load(open(f), Loader=yaml.FullLoader)
        tz = calendar_yaml.get("timezone", None)
        if tz is not None:
            tz = gettz(tz)
        if "include" in calendar_yaml:
            included_events, _name = files_to_events(
                os.path.join(os.path.dirname(f), newfile)
                for newfile in calendar_yaml["include"]
            )
            all_events.extend(included_events)
        for event in calendar_yaml.get("events", []):
            all_events.append(event_from_yaml(event, tz=tz))

        # We can only provide one calendar name, so we'll
        # keep the last one we find
        name = calendar_yaml.get("name", name)

    return all_events, name


def files_to_calendar(files: list) -> ics.Calendar:
    """'main function: list of files to our result"""
    all_events, name = files_to_events(files)

    calendar = events_to_calendar(all_events)
    if name is not None:
        calendar.extra.append(ics.ContentLine(name="NAME", value=name))
        calendar.extra.append(ics.ContentLine(name="X-WR-CALNAME", value=name))
    return calendar


def main():
    if len(sys.argv) < 2:
        print("Usage: yaml2ics.py FILE1.yaml FILE2.yaml ...")
        sys.exit(-1)

    files = sys.argv[1:]
    for f in files:
        if not os.path.isfile(f):
            print(f"Error: {f} is not a file", file=sys.stderr)
            sys.exit(-1)

    calendar = files_to_calendar(files)

    print(calendar.serialize())


if __name__ == "__main__":
    main()
