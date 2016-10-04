#!/usr/bin/env python3

# TODO: get some docstrings in here!

import can
import canmatrix.importany as importany
import epyq.canneo
import epyq.deviceextension
import epyq.nv
import epyq.nvview
import epyq.overlaylabel
import epyq.txrx
import epyq.txrxview
import functools
import importlib.util
import io
import json
import os
import shutil
import tempfile
import zipfile

from collections import OrderedDict
from enum import Enum, unique
from epyq.busproxy import BusProxy
from epyq.widgets.abstractwidget import AbstractWidget
from PyQt5 import uic
from PyQt5.QtCore import pyqtSlot, Qt, QFile, QFileInfo, QTextStream, QObject
from PyQt5.QtWidgets import QWidget

# See file COPYING in this source tree
__copyright__ = 'Copyright 2016, EPC Power Corp.'
__license__ = 'GPLv2+'


@unique
class Elements(Enum):
    dash = 1
    tx = 2
    rx = 3
    nv = 4


@unique
class Tabs(Enum):
    dashes = 1
    txrx = 2
    nv = 3


def j1939_node_id_adjust(message_id, node_id):
    if node_id == 0:
        return message_id

    raise Exception('J1939 node id adjustment not yet implemented')


def simple_node_id_adjust(message_id, node_id):
    return message_id + node_id


node_id_types = OrderedDict([
    ('j1939', j1939_node_id_adjust),
    ('simple', simple_node_id_adjust)
])


def load(file):
    if isinstance(file, str):
        pass
    elif isinstance(file, io.IOBase):
        pass


class Device:
    def __init__(self, *args, **kwargs):
        if kwargs.get('file', None) is not None:
            constructor = self._init_from_file
        else:
            constructor = self._init_from_parameters

        constructor(*args, **kwargs)

    def __del__(self):
        self.bus.set_bus()

    def _init_from_file(self, file, bus=None, elements=None,
                        tabs=None, rx_interval=0, edit_actions=None):
        if elements is None:
            elements = set(Elements)
        if tabs is None:
            tabs = set(Tabs)

        try:
            zip_file = zipfile.ZipFile(file)
        except zipfile.BadZipFile:
            try:
                self.config_path = os.path.abspath(file)
                file = open(file, 'r')
            except TypeError:
                return
            else:
                self._load_config(file=file, bus=bus, elements=elements,
                                  tabs=tabs, rx_interval=rx_interval,
                                  edit_actions=edit_actions)
        else:
            self._init_from_zip(zip_file, bus=bus, elements=elements,
                                tabs=tabs, rx_interval=rx_interval,
                                edit_actions=edit_actions)

    def _load_config(self, file, bus=None, elements=None,
                     tabs=None, rx_interval=0, edit_actions=None):
        if tabs is None:
            tabs = set(Tabs)

        if elements is None:
            elements = set(Elements)
            if Tabs.txrx not in tabs:
                self.elements.discard(Elements.tx)
                self.elements.discard(Elements.rx)

            if Tabs.nv not in tabs:
                self.elements.discard(Elements.nv)

        s = file.read()
        d = json.loads(s, object_pairs_hook=OrderedDict)
        self.raw_dict = d

        module = d.get('module', None)
        self.plugin = None
        if module is None:
            extension_class = epyq.deviceextension.DeviceExtension
        else:
            spec = importlib.util.spec_from_file_location(
                'extension', self.absolute_path(module))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            extension_class = module.DeviceExtension

        self.extension = extension_class(device=self)

        path = os.path.dirname(file.name)
        json_ui_paths = {}
        for ui_path_name in ['ui_path', 'ui_paths', 'menu']:
            try:
                json_ui_paths = d[ui_path_name]
                break
            except KeyError:
                pass

        for tab in Tabs:
            try:
                value = d['tabs'][tab.name]
            except KeyError:
                pass
            else:
                if int(value):
                    tabs.add(tab)
                else:
                    tabs.discard(tab)

        self.ui_paths = OrderedDict()
        try:
            for name, ui_path in json_ui_paths.items():
                self.ui_paths[name] = ui_path
        except AttributeError:
            self.ui_paths["Dash"] = json_ui_paths

        self.can_path = os.path.join(path, d['can_path'])

        self.bus = BusProxy(bus=bus)
        self.node_id_type = d.get('node_id_type',
                                  next(iter(node_id_types))).lower()
        self.node_id = int(d.get('node_id', 0))
        self.node_id_adjust = functools.partial(
            node_id_types[self.node_id_type],
            node_id=self.node_id
        )

        self._init_from_parameters(
            uis=self.ui_paths,
            serial_number=d.get('serial_number', ''),
            name=d.get('name', ''),
            elements=elements,
            tabs=tabs,
            rx_interval=rx_interval,
            edit_actions=edit_actions)

    def _init_from_zip(self, zip_file, bus=None, elements=None,
                       tabs=None, rx_interval=0, edit_actions=None):
        if elements is None:
            elements = set(Elements)
        if tabs is None:
            tabs = set(Tabs)

        path = tempfile.mkdtemp()
        zip_file.extractall(path=path)
        # TODO error dialog if no .epc found in zip file
        for f in os.listdir(path):
            if f.endswith(".epc"):
                file = os.path.join(path, f)
        self.config_path = os.path.abspath(file)
        with open(file, 'r') as file:
            self._load_config(file, bus=bus, elements=elements, tabs=tabs,
                              rx_interval=rx_interval, edit_actions=edit_actions)

        shutil.rmtree(path)

    def _init_from_parameters(self, uis, serial_number, name, bus=None,
                              elements=None, tabs=None,
                              rx_interval=0, edit_actions=None):
        self.elements = set(Elements) if elements == None else set(elements)
        if tabs is None:
            tabs = set(Tabs)

        if not hasattr(self, 'bus'):
            self.bus = BusProxy(bus=bus)

        self.rx_interval = rx_interval
        self.serial_number = serial_number
        self.name = '{name} :{id}'.format(name=name,
                                          id=self.node_id)

        device_ui = 'device.ui'
        # TODO: CAMPid 9549757292917394095482739548437597676742
        if not QFileInfo(device_ui).isAbsolute():
            ui_file = os.path.join(
                QFileInfo.absolutePath(QFileInfo(__file__)), device_ui)
        else:
            ui_file = device_ui
        ui_file = QFile(ui_file)
        ui_file.open(QFile.ReadOnly | QFile.Text)
        ts = QTextStream(ui_file)
        sio = io.StringIO(ts.readAll())
        self.ui = uic.loadUi(sio)
        self.loaded_uis = {}

        def traverse(dict_node):
            for key, value in dict_node.items():
                if isinstance(value, dict):
                    traverse(value)
                elif value.endswith('.ui'):
                    path = value
                    try:
                        dict_node[key] = self.loaded_uis[path]
                    except KeyError:
                        # TODO: CAMPid 9549757292917394095482739548437597676742
                        if not QFileInfo(path).isAbsolute():
                            ui_file = os.path.join(
                                QFileInfo.absolutePath(QFileInfo(self.config_path)),
                                path)
                        else:
                            ui_file = path
                        ui_file = QFile(ui_file)
                        ui_file.open(QFile.ReadOnly | QFile.Text)
                        ts = QTextStream(ui_file)
                        sio = io.StringIO(ts.readAll())
                        dict_node[key] = uic.loadUi(sio)
                        dict_node[key].file_name = path
                        self.loaded_uis[path] = dict_node[key]

        traverse(uis)

        # TODO: yuck, actually tidy the code
        self.dash_uis = uis

        notifiees = []

        if Elements.dash in self.elements:
            self.uis = self.dash_uis

            matrix = list(importany.importany(self.can_path).values())[0]
            # TODO: this is icky
            if Elements.tx not in self.elements:
                self.neo_frames = epyq.canneo.Neo(matrix=matrix,
                                                  bus=self.bus,
                                                  rx_interval=self.rx_interval)

                notifiees.append(self.neo_frames)

        if Elements.rx in self.elements:
            # TODO: the repetition here is not so pretty
            matrix_rx = list(importany.importany(self.can_path).values())[0]
            neo_rx = epyq.canneo.Neo(matrix=matrix_rx,
                                     frame_class=epyq.txrx.MessageNode,
                                     signal_class=epyq.txrx.SignalNode,
                                     node_id_adjust=self.node_id_adjust)

            rx = epyq.txrx.TxRx(tx=False, neo=neo_rx)
            notifiees.append(rx)
            rx_model = epyq.txrx.TxRxModel(rx)

            # TODO: put this all in the model...
            rx.changed.connect(rx_model.changed)
            rx.begin_insert_rows.connect(rx_model.begin_insert_rows)
            rx.end_insert_rows.connect(rx_model.end_insert_rows)

        if Elements.tx in self.elements:
            matrix_tx = list(importany.importany(self.can_path).values())[0]
            message_node_tx_partial = functools.partial(epyq.txrx.MessageNode,
                                                        tx=True)
            signal_node_tx_partial = functools.partial(epyq.txrx.SignalNode,
                                                       tx=True)
            neo_tx = epyq.canneo.Neo(matrix=matrix_tx,
                                     frame_class=message_node_tx_partial,
                                     signal_class=signal_node_tx_partial,
                                     node_id_adjust=self.node_id_adjust)
            notifiees.extend(neo_tx.frames)

            self.neo_frames = neo_tx

            tx = epyq.txrx.TxRx(tx=True, neo=neo_tx, bus=self.bus)
            tx_model = epyq.txrx.TxRxModel(tx)
            tx.changed.connect(tx_model.changed)

        # TODO: something with sets instead?
        if (Elements.rx in self.elements or
            Elements.tx in self.elements):
            txrx_views = self.ui.findChildren(epyq.txrxview.TxRxView)
            if len(txrx_views) > 0:
                # TODO: actually find them and actually support multiple
                self.ui.rx.setModel(rx_model)
                self.ui.tx.setModel(tx_model)

        if Elements.nv in self.elements:
            matrix_nv = list(importany.importany(self.can_path).values())[0]
            self.frames_nv = epyq.canneo.Neo(
                matrix=matrix_nv,
                frame_class=epyq.nv.Frame,
                signal_class=epyq.nv.Nv,
                node_id_adjust=self.node_id_adjust
            )

            self.nvs = epyq.nv.Nvs(self.frames_nv, self.bus)
            notifiees.append(self.nvs)


            nv_views = self.ui.findChildren(epyq.nvview.NvView)
            if len(nv_views) > 0:
                nv_model = epyq.nv.NvModel(self.nvs)
                self.nvs.changed.connect(nv_model.changed)

                for view in nv_views:
                    view.setModel(nv_model)

        if Tabs.dashes in tabs:
            for i, (name, dash) in enumerate(self.dash_uis.items()):
                self.ui.tabs.insertTab(i,
                                       dash,
                                       name)
        if Tabs.txrx not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.txrx))
        if Tabs.nv not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.nv))
        if tabs:
            self.ui.offline_overlay = epyq.overlaylabel.OverlayLabel(parent=self.ui)
            self.ui.offline_overlay.label.setText('offline')

            self.ui.name.setText(name)
            self.ui.tabs.setCurrentIndex(0)



        if Tabs.dashes in tabs:
            for i, (name, dash) in enumerate(self.dash_uis.items()):
                self.ui.tabs.insertTab(i,
                                       dash,
                                       name)
        if Tabs.txrx not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.txrx))
        if Tabs.nv not in tabs:
            self.ui.tabs.removeTab(self.ui.tabs.indexOf(self.ui.nv))
        if tabs:
            self.ui.offline_overlay = epyq.overlaylabel.OverlayLabel(parent=self.ui)
            self.ui.offline_overlay.label.setText('offline')

            self.ui.name.setText(self.name)
            self.ui.tabs.setCurrentIndex(0)



        notifier = self.bus.notifier
        for notifiee in notifiees:
            notifier.add(notifiee)

        def flatten(dict_node):
            flat = set()
            for key, value in dict_node.items():
                if isinstance(value, dict):
                    flat |= flatten(value)
                else:
                    flat.add(value)

            return flat

        flat = flatten(self.dash_uis)
        flat = [v for v in flat if isinstance(v, QWidget)]

        self.dash_connected_signals = set()
        self.dash_missing_signals = set()
        for dash in flat:
            # TODO: CAMPid 99457281212789437474299
            children = dash.findChildren(QObject)
            widgets = [c for c in children if
                       isinstance(c, AbstractWidget)]

            dash.connected_frames = set()
            frames = dash.connected_frames

            for widget in widgets:
                frame_name = widget.property('frame')
                signal_name = widget.property('signal')

                widget.set_range(min=0, max=100)
                widget.set_value(42)

                # TODO: add some notifications
                found = False
                frame = self.neo_frames.frame_by_name(frame_name)
                if frame is not None:
                    signal = frame.signal_by_name(signal_name)
                    if signal is not None:
                        found = True
                        frames.add(frame)
                        self.dash_connected_signals.add(signal)
                        widget.set_signal(signal)
                        frame.user_send_control = False

                if edit_actions is not None:
                    # TODO: CAMPid 97453289314763416967675427
                    if widget.property('editable'):
                        for action in edit_actions:
                            if action[1](widget):
                                action[0](dash=dash,
                                          widget=widget,
                                          signal=widget.edit)
                                break
                if not found:
                    self.dash_missing_signals.add(
                        '{} : {}'.format(frame_name, signal_name))

        self.bus_status_changed(online=False, transmit=False)

        all_signals = set()
        for frame in self.neo_frames.frames:
            for signal in frame.signals:
                if signal.name != '__padding__':
                    all_signals.add(signal)

        frame_signals = []
        for signal in all_signals - self.dash_connected_signals:
            frame_signals.append('{} : {}'.format(signal.frame.name, signal.name))

        if Elements.nv in self.elements:
            nv_frame_signals = []
            for frame in (list(self.nvs.set_frames.values())
                              + list(self.nvs.status_frames.values())):
                for signal in frame.signals:
                    nv_frame_signals.append(
                        '{} : {}'.format(signal.frame.name, signal.name))

            frame_signals = list(set(frame_signals) - set(nv_frame_signals))

        if len(frame_signals) > 0:
            print('\n === Signals not referenced by a widget')
            for frame_signal in sorted(frame_signals):
                print(frame_signal)

        if len(self.dash_missing_signals) > 0:
            print('\n === Signals referenced by a widget but not defined')
            for frame_signal in sorted(self.dash_missing_signals):
                print(frame_signal)

        self.extension.post()

    def absolute_path(self, path=''):
        # TODO: CAMPid 9549757292917394095482739548437597676742
        if not QFileInfo(path).isAbsolute():
            path = os.path.join(
                QFileInfo.absolutePath(QFileInfo(self.config_path)),
                path)

        return path

    def get_frames(self):
        return self.frames

    @pyqtSlot(bool)
    def bus_status_changed(self, online, transmit):
        style = epyq.overlaylabel.styles['red']
        text = ''
        if online:
            if not transmit:
                text = 'passive'
                style = epyq.overlaylabel.styles['blue']
        else:
            text = 'offline'

        try:
            offline_overlay = self.ui.offline_overlay
        except AttributeError:
            pass
        else:
            offline_overlay.label.setText(text)
            offline_overlay.setVisible(len(text) > 0)
            offline_overlay.setStyleSheet(style)


if __name__ == '__main__':
    import sys

    print('No script functionality here')
    sys.exit(1)     # non-zero is a failure