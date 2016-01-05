#!/usr/bin/env python3.4

# TODO: get some docstrings in here!

import can
import canmatrix.importany as importany
import copy
import epyq.canneo
import epyq.txrx
import functools
import math
import os
import platform
from PyQt5 import QtCore, QtWidgets, QtGui, uic
import sys
import time

# See file COPYING in this source tree
__copyright__ = 'Copyright 2015, EPC Power Corp.'
__license__ = 'GPLv2+'


class Window(QtWidgets.QMainWindow):
    def __init__(self, matrix, tx_model, rx_model, parent=None):
        QtWidgets.QMainWindow.__init__(self, parent=parent)

        ui_file = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'main.ui')
        self.ui = uic.loadUi(ui_file, self)
        self.ui.rx.setModel(rx_model)
        self.ui.tx.setModel(tx_model)

        children = self.findChildren(QtCore.QObject)
        targets = [c for c in children if
                   c.property('frame') and c.property('signal')]

        # TODO: make this accessible in Designer
        self.ui.other_scale.setOrientations(QtCore.Qt.Vertical)
        # self.ui.scale.setOrientations(QtCore.Qt.Horizontal)

        for target in targets:
            frame_name = target.property('frame')
            signal_name = target.property('signal')

            frame = matrix.frameByName(frame_name).frame
            signal = frame.frame.signalByName(signal_name).signal

            # TODO: clearly shouldn't be hardcoded
            if frame_name == 'StatusControlVolts2':
                if signal_name == 'n15V_Supply':
                    breakpoints = [-17, -16, -14, -13]
                    colors = [
                        QtCore.Qt.darkRed,
                        QtCore.Qt.darkYellow,
                        QtCore.Qt.darkGreen,
                        QtCore.Qt.darkYellow,
                        QtCore.Qt.darkRed
                    ]
            else:
                breakpoints = [75, 90]
                colors = [QtCore.Qt.darkGreen, QtCore.Qt.darkYellow, QtCore.Qt.darkRed]

            try:
                target.setColorRanges(colors, breakpoints)
            except AttributeError:
                pass

            signal.connect(target.setValue)
            target.setRange(float(signal.signal._min),
                            float(signal.signal._max))


def main(args=None):
    import sys

    if args is None:
        import argparse

        can_file = os.path.join(
            os.path.dirname(os.path.realpath(__file__)),
            '..',
            'tests',
            'AFE_CAN_ID247_FACTORY.sym')

        parser = argparse.ArgumentParser()
        parser.add_argument('--can', default=can_file)
        parser.add_argument('--generate', '-g', action='store_true')
        args = parser.parse_args()

    # TODO: get this outta here
    default = {
        'Linux': {'bustype': 'socketcan', 'channel': 'vcan0'},
        'Windows': {'bustype': 'pcan', 'channel': 'PCAN_USBBUS1'}
    }[platform.system()]
    bus = can.interface.Bus(**default)

    # TODO: the repetition here is not so pretty
    matrix_rx = importany.importany(args.can)
    epyq.canneo.neotize(matrix=matrix_rx,
                        frame_class=epyq.txrx.MessageNode,
                        signal_class=epyq.txrx.SignalNode)

    matrix_tx = importany.importany(args.can)
    message_node_tx_partial = functools.partial(epyq.txrx.MessageNode,
                                                tx=True)
    signal_node_tx_partial = functools.partial(epyq.txrx.SignalNode,
                                               tx=True)
    epyq.canneo.neotize(matrix=matrix_tx,
                        frame_class=message_node_tx_partial,
                        signal_class=signal_node_tx_partial)

    matrix_widgets = importany.importany(args.can)
    # TODO: these should probably be just canneo objects
    frames_widgets = epyq.canneo.neotize(
            matrix=matrix_widgets,
            frame_class=epyq.txrx.MessageNode,
            signal_class=epyq.txrx.SignalNode)

    rx = epyq.txrx.TxRx(tx=False, matrix=matrix_rx)
    rx_model = epyq.txrx.TxRxModel(rx)

    rx.changed.connect(rx_model.changed)
    rx.added.connect(rx_model.added)

    tx = epyq.txrx.TxRx(tx=True, matrix=matrix_tx, bus=bus)
    tx_model = epyq.txrx.TxRxModel(tx)

    tx.changed.connect(tx_model.changed)
    tx.added.connect(tx_model.added)
    notifier = can.Notifier(bus, frames_widgets + [rx], timeout=0.1)

    if args.generate:
        print('generating')
        start_time = time.monotonic()

        frame_name = 'StatusControlVolts2'
        signal_name = 'n15V_Supply'
        frame = epyq.canneo.Frame(matrix_tx.frameByName(frame_name))
        signal = epyq.canneo.Signal(frame.frame.signalByName(signal_name), frame)

        message = can.Message(extended_id=frame.frame._extended,
                              arbitration_id=frame.frame._Id,
                              dlc=frame.frame._Size)

        messages = [
            can.Message(extended_id=True,
                        arbitration_id=486517239,
                        dlc=8,
                        data=bytearray([0, 1, 0, 160, 7, 208, 5, 220])),
            can.Message(extended_id=True,
                        arbitration_id=486517239,
                        dlc=8,
                        data=bytearray([0, 4, 0, 160, 1, 77, 0, 160])),
            can.Message(extended_id=True,
                        arbitration_id=218082369,
                        dlc=8,
                        data=bytearray([0, 0, 0, 3, 0, 0, 0, 42]))
        ]

        # Copy from PCAN generated and logged messages
        # Bus=2,ID=486517239x,Type=D,DLC=8,DA=0,Data=0 1 0 160 7 208 5 220 ,
        # Bus=2,ID=486517239x,Type=D,DLC=8,DA=0,Data=0 4 0 160 1 77 0 160 ,
        # Bus=2,ID=218082369x,Type=D,DLC=8,DA=0,Data=0 0 0 3 0 0 0 42 ,

        last_send = 0
        while True:
            time.sleep(0.010)
            now = time.monotonic()
            if now - last_send > 0.100:
                last_send = now
                elapsed_time = time.monotonic() - start_time
                value = math.sin(elapsed_time) / 2
                value *= 2
                value -= 15
                value /= float(signal.signal._factor)
                value = round(value)
                print('{:.3f}: {}'.format(elapsed_time, value))
                message.data = frame.pack([value, 0, 1, 2])
                bus.send(message)

                for m in messages:
                    bus.send(m)
        sys.exit(0)

    from PyQt5.QtWidgets import QApplication

    app = QApplication(sys.argv)

    window = Window(matrix_widgets, tx_model=tx_model, rx_model=rx_model)

    window.show()
    return app.exec_()

if __name__ == '__main__':
    sys.exit(main())
