#!/usr/bin/env python3

# Written by Sultan Qasim Khan
# Copyright (c) 2019-2024, NCC Group plc
# Released as open source under GPLv3

import struct
from traceback import print_exception
from .sniffle_hw import PacketMessage
from .crc_ble import rbit24
from .constants import BLE_ADV_AA
from .sniffer_state import SnifferState
from .decoder_state import SniffleDecoderState

def str_mac(mac):
    return ":".join(["%02X" % b for b in reversed(mac)])

def _str_atype(addr, is_random):
    # Non-resolvable private address
    # Resolvable private address
    # Reserved for future use
    # Static device address
    if not is_random: return "Public"
    atypes = ["NRPA", "RPA", "RFU", "Static"]
    atype = addr[5] >> 6
    return atypes[atype]

def str_mac2(mac, is_random):
    return "%s (%s)" % (str_mac(mac), _str_atype(mac, is_random))

def printable(s):
    pchar = lambda a: chr(a) if 32 <= a < 127 else '.'
    return ''.join([pchar(a) for a in s])

def hexline(s, bytes_per_group=8):
    chunks = []
    for i in range(0, len(s), bytes_per_group):
        chunks.append(' '.join([f'{c:02x}' for c in s[i:i+bytes_per_group]]))
    return '  '.join(chunks)

def hexdump(s, bytes_per_line=16, bytes_per_group=8):
    prev_chunk = None
    in_repeat = False
    hexline_len = 3*bytes_per_line + bytes_per_line//bytes_per_group - 2
    lines = []
    for i in range(0, len(s), bytes_per_line):
        chunk = s[i:i+bytes_per_line]
        if chunk == prev_chunk and i + bytes_per_line < len(s):
            if not in_repeat:
                lines.append('*')
                in_repeat = True
        else:
            lines.append(f'0x{i:04x}:  {hexline(chunk, bytes_per_group):{hexline_len}}  {printable(chunk)}')
            in_repeat = False
        prev_chunk = chunk
    return '\n'.join(lines)

class DPacketMessage(PacketMessage):
    pdutype = "RFU"

    # copy constructor, deliberately no call to super()
    def __init__(self, pkt: PacketMessage):
        self.ts = pkt.ts
        self.ts_epoch = pkt.ts_epoch
        self.aa = pkt.aa
        self.rssi = pkt.rssi
        self.chan = pkt.chan
        self.phy = pkt.phy
        self.body = pkt.body
        self.data_dir = pkt.data_dir
        self.event = pkt.event
        self.crc_rev = pkt.crc_rev

    def hexdump(self):
        return hexdump(self.body)

    def __str__(self):
        return "\n".join([self.str_header(), self.hexdump()])

    @classmethod
    def from_body(cls, body, is_data=False, slave_send=False):
        return cls.decode(super().from_body(body, is_data, slave_send))

    @staticmethod
    def decode(pkt: PacketMessage, dstate=None):
        try:
            if pkt.aa == BLE_ADV_AA:
                dpkt = AdvertMessage.decode(pkt, dstate)
            else:
                dpkt = DataMessage.decode(pkt, dstate)

            if dstate:
                update_state(dpkt, dstate)
            return dpkt
        except Exception as e:
            # TODO: nicer error handling and write to logger
            print("Parse error!")
            print_exception(e)
            return pkt

class AdvertMessage(DPacketMessage):
    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.ChSel = (self.body[0] >> 5) & 1
        self.TxAdd = (self.body[0] >> 6) & 1
        self.RxAdd = (self.body[0] >> 7) & 1
        self.ad_length = self.body[1]

    def str_adtype(self):
        atstr = "Ad Type: %s\n" % self.pdutype
        atstr += "ChSel: %i " % self.ChSel
        atstr += "TxAdd: %i " % self.TxAdd
        atstr += "RxAdd: %i " % self.RxAdd
        atstr += "Ad Length: %i" % self.ad_length
        return atstr

    def __str__(self):
        return "\n".join([self.str_header(), self.str_adtype(), self.hexdump()])

    @staticmethod
    def decode(pkt: PacketMessage, dstate=None):
        pdu_type = pkt.body[0] & 0xF
        if pkt.chan >= 37:
            type_classes = [
                    AdvIndMessage,          # 0
                    AdvDirectIndMessage,    # 1
                    AdvNonconnIndMessage,   # 2
                    ScanReqMessage,         # 3
                    ScanRspMessage,         # 4
                    ConnectIndMessage,      # 5
                    AdvScanIndMessage,      # 6
                    AdvExtIndMessage]       # 7
            if pdu_type < len(type_classes):
                tc = type_classes[pdu_type]
            else:
                tc = AdvertMessage
        else:
            if pdu_type == 3:
                tc = AuxScanReqMessage
            elif pdu_type == 5:
                tc = AuxConnectReqMessage
            elif pdu_type == 7:
                if dstate.aux_pending_scan_rsp and \
                        pkt.ts < dstate.aux_pending_scan_rsp:
                    tc = AuxScanRspMessage
                elif dstate.aux_pending_chain and \
                        pkt.chan == dstate.aux_pending_chain[1] and \
                        pkt.ts < dstate.aux_pending_chain[2]:
                    # TODO: check ADI match
                    tc = AuxChainIndMessage
                else:
                    tc = AuxAdvIndMessage
            elif pdu_type == 8:
                tc = AuxConnectRspMessage
            else:
                tc = AdvertMessage

        return tc(pkt)

class DataMessage(DPacketMessage):
    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.NESN = (self.body[0] >> 2) & 1
        self.SN = (self.body[0] >> 3) & 1
        self.MD = (self.body[0] >> 4) & 1
        self.data_length = self.body[1]

    def str_datatype(self):
        dtstr = "LLID: %s\n" % self.pdutype
        dtstr += "Dir: %s " % ("S->M" if self.data_dir else "M->S")
        dtstr += "NESN: %i " % self.NESN
        dtstr += "SN: %i " % self.SN
        dtstr += "MD: %i " % self.MD
        dtstr += "Data Length: %i" % self.data_length
        return dtstr

    def str_header(self):
        phy_names = ["1M", "2M", "Coded (S=8)", "Coded (S=2)"]
        return "Timestamp: %.6f\tLength: %i\tRSSI: %i\tChannel: %i\tPHY: %s\tEvent: %d" % (
            self.ts, len(self.body), self.rssi, self.chan, phy_names[self.phy], self.event)

    def __str__(self):
        return "\n".join([self.str_header(), self.str_datatype(), self.hexdump()])

    @staticmethod
    def decode(pkt: PacketMessage, dstate=None):
        LLID = pkt.body[0] & 0x3
        type_classes = [
                DataMessage,        # 0 (RFU)
                LlDataContMessage,  # 1
                LlDataMessage,      # 2
                LlControlMessage]   # 3
        return type_classes[LLID](pkt)

class LlDataMessage(DataMessage):
    pdutype = "LL DATA"

class LlDataContMessage(DataMessage):
    pdutype = "LL DATA CONT"

class LlControlMessage(DataMessage):
    pdutype = "LL CONTROL"

    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.opcode = self.body[2]

    def str_opcode(self):
        control_opcodes = [
                "LL_CONNECTION_UPDATE_IND",
                "LL_CHANNEL_MAP_IND",
                "LL_TERMINATE_IND",
                "LL_ENC_REQ",
                "LL_ENC_RSP",
                "LL_START_ENC_REQ",
                "LL_START_ENC_RSP",
                "LL_UNKNOWN_RSP",
                "LL_FEATURE_REQ",
                "LL_FEATURE_RSP",
                "LL_PAUSE_ENC_REQ",
                "LL_PAUSE_ENC_RSP",
                "LL_VERSION_IND",
                "LL_REJECT_IND",
                "LL_SLAVE_FEATURE_REQ",
                "LL_CONNECTION_PARAM_REQ",
                "LL_CONNECTION_PARAM_RSP",
                "LL_REJECT_EXT_IND",
                "LL_PING_REQ",
                "LL_PING_RSP",
                "LL_LENGTH_REQ",
                "LL_LENGTH_RSP",
                "LL_PHY_REQ",
                "LL_PHY_RSP",
                "LL_PHY_UPDATE_IND",
                "LL_MIN_USED_CHANNELS_IND"
                ]
        if self.opcode < len(control_opcodes):
            return "Opcode: %s" % control_opcodes[self.opcode]
        else:
            return "Opcode: RFU (0x%02X)" % self.opcode

    def __str__(self):
        return "\n".join([
            self.str_header(),
            self.str_datatype(),
            self.str_opcode(),
            self.hexdump()])

class AdvaMessage(AdvertMessage):
    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.AdvA = self.body[2:8]

    def str_adva(self):
        return "AdvA: %s" % str_mac2(self.AdvA, self.TxAdd)

    def __str__(self):
        return "\n".join([
            self.str_header(),
            self.str_adtype(),
            self.str_adva(),
            self.hexdump()])

class AdvIndMessage(AdvaMessage):
    pdutype = "ADV_IND"

class AdvNonconnIndMessage(AdvaMessage):
    pdutype = "ADV_NONCONN_IND"

class ScanRspMessage(AdvaMessage):
    pdutype = "SCAN_RSP"

class AdvScanIndMessage(AdvaMessage):
    pdutype = "ADV_SCAN_IND"

class AdvDirectIndMessage(AdvertMessage):
    pdutype = "ADV_DIRECT_IND"

    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.AdvA = self.body[2:8]
        self.TargetA = self.body[8:14]

    def str_ata(self):
        return "AdvA: %s TargetA: %s" % (str_mac2(self.AdvA, self.TxAdd), str_mac2(self.TargetA, self.RxAdd))

    def __str__(self):
        return "\n".join([
            self.str_header(),
            self.str_adtype(),
            self.str_ata(),
            self.hexdump()])

class ScanReqMessage(AdvertMessage):
    pdutype = "SCAN_REQ"

    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.ScanA = self.body[2:8]
        self.AdvA = self.body[8:14]

    def str_asa(self):
        return "ScanA: %s AdvA: %s" % (str_mac2(self.ScanA, self.TxAdd), str_mac2(self.AdvA, self.RxAdd))

    def __str__(self):
        return "\n".join([
            self.str_header(),
            self.str_adtype(),
            self.str_asa(),
            self.hexdump()])

class AuxScanReqMessage(ScanReqMessage):
    pdutype = "AUX_SCAN_REQ"

class ConnectIndMessage(AdvertMessage):
    pdutype = "CONNECT_IND"

    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.InitA = self.body[2:8]
        self.AdvA = self.body[8:14]
        self.aa_conn = struct.unpack('<L', self.body[14:18])[0]
        self.CRCInit = self.body[18] | (self.body[19] << 8) | (self.body[20] << 16)
        self.WinSize = self.body[21]
        self.WinOffset, self.Interval, self.Latency, self.Timeout = struct.unpack(
                "<HHHH", self.body[22:30])
        self.ChM = self.body[30:35]
        self.Hop = self.body[35] & 0x1F
        self.SCA = self.body[35] >> 5

    def str_aia(self):
        return "InitA: %s AdvA: %s AA: 0x%08X CRCInit: 0x%06X" % (
                str_mac2(self.InitA, self.TxAdd), str_mac2(self.AdvA, self.RxAdd), self.aa_conn, self.CRCInit)

    def str_conn_params(self):
        return "WinSize: %d WinOffset: %d Interval: %d Latency: %d Timeout: %d Hop: %d SCA: %d" % (
                self.WinSize, self.WinOffset, self.Interval, self.Latency, self.Timeout,
                self.Hop, self.SCA)

    def str_chm(self):
        if self.ChM == b'\xFF\xFF\xFF\xFF\x1F':
            descstr = "all channels"
        else:
            has_chan = lambda chm, i: (chm[i // 8] & (1 << (i & 7))) != 0
            excludes = []
            for i in range(37):
                if not has_chan(self.ChM, i):
                    excludes.append(i)
            descstr = "excludes " + ", ".join([str(i) for i in excludes])
        chanstr = "%02X %02X %02X %02X %02X" % tuple(self.ChM)
        return "Channel Map: %s (%s)" % (chanstr, descstr)

    def __str__(self):
        return "\n".join([
            self.str_header(),
            self.str_adtype(),
            self.str_aia(),
            self.str_conn_params(),
            self.str_chm(),
            self.hexdump()])

class AuxConnectReqMessage(ConnectIndMessage):
    pdutype = "AUX_CONNECT_REQ"

class AuxPtr:
    def __init__(self, ptr):
        self.chan = ptr[0] & 0x3F
        self.phy = ptr[2] >> 5
        offsetMult = 300 if ptr[0] & 0x80 else 30
        auxOffset = ptr[1] + ((ptr[2] & 0x1F) << 8)
        self.offsetUsec = auxOffset * offsetMult

    def __str__(self):
        phy_names = ["1M", "2M", "Coded", "Invalid3", "Invalid4",
                "Invalid5", "Invalid6", "Invalid7"]
        return "AuxPtr Chan: %d PHY: %s Delay: %d us" % (
            self.chan, phy_names[self.phy], self.offsetUsec)

class AdvDataInfo:
    def __init__(self, adi):
        self.did = adi[0] + ((adi[1] & 0x0F) << 8)
        self.sid = adi[1] >> 4

    def __str__(self):
        return "AdvDataInfo DID: 0x%03x SID: 0x%01x" % (self.did, self.sid)

class AdvExtIndMessage(AdvertMessage):
    pdutype = "ADV_EXT_IND"

    def __init__(self, pkt: PacketMessage):
        super().__init__(pkt)
        self.AdvA = None
        self.TargetA = None
        self.CTEInfo = None
        self.AdvDataInfo = None
        self.AuxPtr = None
        self.SyncInfo = None
        self.TxPower = None
        self.ACAD = None

        try:
            if len(self.body) < 3:
                raise ValueError("Extended advertisement too short!")
            self.AdvMode = self.body[2] >> 6 # Neither, Connectable, Scannable, or RFU
            hdrBodyLen = self.body[2] & 0x3F

            if len(self.body) < hdrBodyLen + 1:
                raise ValueError("Inconistent header length!")

            hdrFlags = self.body[3]
            hdrPos = 4
            dispMsgs = []

            if hdrFlags & 0x01:
                self.AdvA = self.body[hdrPos:hdrPos+6]
                hdrPos += 6
            if hdrFlags & 0x02:
                self.TargetA = self.body[hdrPos:hdrPos+6]
                hdrPos += 6
            if hdrFlags & 0x04:
                self.CTEInfo = self.body[hdrPos]
                hdrPos += 1
            if hdrFlags & 0x08:
                self.AdvDataInfo = AdvDataInfo(self.body[hdrPos:hdrPos+2])
                hdrPos += 2
            if hdrFlags & 0x10:
                self.AuxPtr = AuxPtr(self.body[hdrPos:hdrPos+3])
                hdrPos += 3
            if hdrFlags & 0x20:
                # TODO decode this nicely
                self.SyncInfo = self.body[hdrPos:hdrPos+18]
                hdrPos += 18
            if hdrFlags & 0x40:
                self.TxPower = struct.unpack("b", self.body[hdrPos:hdrPos+1])[0]
                hdrPos += 1
            if hdrPos - 3 < hdrBodyLen:
                ACADLen = hdrBodyLen - (hdrPos - 3)
                self.ACAD = self.body[hdrPos:hdrPos+ACADLen]
                hdrPos += ACADLen
        except Exception as e:
            # TODO: nicer error handling
            print("Parse error!", repr(e))

    def str_aext(self):
        amodes = ["Non-connectable, non-scannable",
                "Connectable", "Scannable", "RFU"]
        modemsg = "AdvMode: %s\n" % amodes[self.AdvMode]

        dispMsgs = []
        if self.AdvA:
            dispMsgs.append("AdvA: %s" % str_mac2(self.AdvA, self.TxAdd))
        if self.TargetA:
            dispMsgs.append("TargetA: %s" % str_mac2(self.TargetA, self.RxAdd))
        if self.CTEInfo:
            dispMsgs.append("CTEInfo: 0x%02X" % self.CTEInfo)
        if self.AdvDataInfo:
            dispMsgs.append(str(self.AdvDataInfo))
        if self.SyncInfo:
            # TODO decode this nicely
            dispMsgs.append("SyncInfo: %s" % repr(self.SyncInfo))
        if self.TxPower:
            dispMsgs.append("TxPower: %d" % self.TxPower)
        if self.ACAD:
            # TODO: pretty print, hex?
            dispMsgs.append("ACAD: %s" % repr(self.ACAD))

        dmsg = modemsg + " ".join(dispMsgs)
        if self.AuxPtr:
            return "\n".join([str(self.AuxPtr), dmsg])
        else:
            return dmsg

    def __str__(self):
        return "\n".join([
            self.str_header(),
            self.str_adtype(),
            self.str_aext(),
            self.hexdump()])

class AuxAdvIndMessage(AdvExtIndMessage):
    pdutype = "AUX_ADV_IND"

class AuxScanRspMessage(AuxAdvIndMessage):
    pdutype = "AUX_SCAN_RSP"

class AuxChainIndMessage(AuxAdvIndMessage):
    pdutype = "AUX_CHAIN_IND"

class AuxConnectRspMessage(AdvExtIndMessage):
    pdutype = "AUX_CONNECT_RSP"

def update_state(pkt: DPacketMessage, dstate: SniffleDecoderState):
    if isinstance(pkt, ConnectIndMessage):
        if pkt.chan < 37 and dstate.last_state != SnifferState.ADVERTISING_EXT:
            dstate.aux_pending_aa = pkt.aa_conn
            dstate.aux_pending_crci = pkt.CRCInit
        else:
            dstate.cur_aa = pkt.aa_conn
            dstate.crc_init_rev = rbit24(pkt.CRCInit)
    elif isinstance(pkt, AuxConnectRspMessage):
        dstate.cur_aa = dstate.aux_pending_aa
        dstate.aux_pending_aa = None
        dstate.crc_init_rev = rbit24(dstate.aux_pending_crci)
        dstate.aux_pending_crci = None
    elif isinstance(pkt, AuxScanReqMessage):
        dstate.aux_pending_scan_rsp = pkt.ts + 0.0005
    elif isinstance(pkt, AuxAdvIndMessage) and pkt.AuxPtr:
        dstate.aux_pending_chain = (pkt.AdvDataInfo, pkt.AuxPtr.chan,
                                    pkt.ts + pkt.AuxPtr.offsetUsec*1E-6 + 0.0005)
    elif dstate.last_state == SnifferState.SCANNING and \
            isinstance(pkt, AuxAdvIndMessage) and \
            pkt.AdvMode == 2: # scannable
        dstate.aux_pending_scan_rsp = pkt.ts + 0.001

    # Clear pending flags as appropriate
    if dstate.aux_pending_scan_rsp:
        if pkt.ts > dstate.aux_pending_scan_rsp or \
                isinstance(pkt, AuxScanRspMessage):
            dstate.aux_pending_scan_rsp = None
    if dstate.aux_pending_chain:
        if pkt.ts > dstate.aux_pending_chain[2]:
            dstate.aux_pending_chain = None
        elif isinstance(pkt, AuxChainIndMessage) and \
                pkt.chan == dstate.aux_pending_chain[1] and \
                pkt.AdvDataInfo == dstate.aux_pending_chain[0]:
            dstate.aux_pending_chain = None