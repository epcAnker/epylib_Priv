import csv
import decimal
import io
import itertools
import operator
import pathlib

import attr
import twisted.internet
import twisted.internet.defer
import twisted.internet.task

import epyqlib.utils.general
import epyqlib.utils.twisted


class TimeParseError(Exception):
    pass


class NoEventsError(epyqlib.utils.general.ExpectedException):
    def expected_message(self):
        return 'No script events found.'


@attr.s(frozen=True)
class Event:
    time = attr.ib()
    action = attr.ib()


operators = {
    '+': operator.add,
    '-': operator.sub,
}


@attr.s(frozen=True)
class Action:
    signal = attr.ib()
    value = attr.ib()
    is_nv = attr.ib(default=False)

    def __call__(self, nvs=None):
        if self is pause_sentinel:
            print('pausing')
            return

        if self.is_nv:
            return self.nv_handler(nvs)
        else:
            return self.standard_handler()

    def standard_handler(self):
        print('standard setting:', self.signal.name, self.value)
        self.signal.set_human_value(self.value)

    def nv_handler(self, nvs):
        print('nv setting:', self.signal.name, self.value)
        self.signal.set_human_value(self.value)
        nvs.write_all_to_device(only_these=(self.signal,))


pause_sentinel = Action(signal=[], value=0)


def csv_load(f):
    events = []

    reader = csv.reader(line for line in f if line.lstrip()[0] != '#')

    last_event_time = 0

    for i, row in enumerate(reader):
        raw_event_time = row[0]

        selected_operator = operators.get(raw_event_time[0], None)
        if selected_operator is not None:
            raw_event_time = raw_event_time[1:]

        try:
            event_time = decimal.Decimal(raw_event_time)
        except decimal.InvalidOperation as e:
            raise TimeParseError(
                'Unable to parse as a time (line {number}): {string}'.format(
                    number=i,
                    string=raw_event_time,
                )
            ) from e

        if selected_operator is not None:
            event_time = selected_operator(last_event_time, event_time)

        raw_actions = [x.strip() for x in row[1:] if len(x) > 0]
        print(list(epyqlib.utils.general.grouper(raw_actions, n=2)))
        actions = []
        for path, value in epyqlib.utils.general.grouper(raw_actions, n=2):
            if path == 'pause':
                actions.append(pause_sentinel)
            else:
                actions.append(Action(
                    signal=path.split(';'),
                    value=decimal.Decimal(value),
                ))

        events.extend([
            Event(time=event_time, action=action)
            for action in actions
        ])

        last_event_time = event_time

    return sorted(events, key=lambda event: event.time)


def csv_loads(s):
    f = io.StringIO(s)
    return csv_load(f)


def csv_loadp(path):
    with open(path) as f:
        return csv_load(f)


def resolve(event, tx_neo, nvs):
    if event.action is pause_sentinel:
        return event

    signal = tx_neo.signal_by_path(*event.action.signal)

    # TODO: CAMPid 079320743340327834208
    is_nv = signal.frame.id == nvs.set_frames[0].id
    if is_nv:
        print('switching', event.action.signal)
        signal = nvs.neo.signal_by_path(*event.action.signal)

    # TODO: remove this backwards compat and just use recent
    #       attrs everywhere
    evolve = getattr(attr, 'evolve', attr.assoc)

    return evolve(
        event,
        action=evolve(
            event.action,
            signal=signal,
            is_nv=is_nv,
        )
    )


def resolve_signals(events, tx_neo, nvs):
    return [
        resolve(event=event, tx_neo=tx_neo, nvs=nvs)
        for event in events
    ]


def run(events, nvs, pause, loop):
    sequence = epyqlib.utils.twisted.Sequence()

    zero_padded = itertools.chain(
        (Event(time=0, action=None),),
        events,
    )

    for p, n in epyqlib.utils.general.pairwise(zero_padded):
        if n.action is pause_sentinel:
            def action(n=n):
                n.action()
                pause()
            kwargs = {}
        else:
            action = n.action
            kwargs = dict(nvs=nvs)

        sequence.add_delayed(
            delay=float(n.time - p.time),
            f=action,
            **kwargs,
        )

    sequence.run(loop=loop)

    return sequence


@attr.s
class Model:
    tx_neo = attr.ib()
    nvs = attr.ib()

    def demo(self):
        events = epyqlib.scripting.csv_loadp(
            pathlib.Path(__file__).parents[0] / 'scripting.csv',
        )

        events = epyqlib.scripting.resolve_signals(
            events=events,
            tx_neo=self.tx_neo,
            nvs=self.nvs,
        )
        epyqlib.scripting.run(events=events, nvs=self.nvs)

    def run_s(self, event_string, pause, loop=False):
        events = csv_loads(event_string)

        if len(events) == 0:
            raise NoEventsError()

        events = epyqlib.scripting.resolve_signals(
            events=events,
            tx_neo=self.tx_neo,
            nvs=self.nvs,
        )

        return run(events=events, nvs=self.nvs, pause=pause, loop=loop)
