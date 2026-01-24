# core/snmp.py
# SNMP Reader with SERIAL NUMBER support for:
# - Xerox
# - Canon iR / iR ADVANCE
# - Ricoh / Kyocera fallback
# Works with your dashboard auto-serial loader

from pysnmp.hlapi import (
    SnmpEngine, CommunityData, UdpTransportTarget,
    ContextData, ObjectType, ObjectIdentity, getCmd
)

SNMP_COMMUNITY = "public"
SNMP_TIMEOUT = 0.8
SNMP_RETRIES = 0

# ======================================================
# COUNTER OIDS (unchanged)
# ======================================================
OID_CANDIDATES = [
    "1.3.6.1.2.1.43.10.2.1.4.1.1",       # Standard Page Counter
    "1.3.6.1.2.1.43.11.1.1.6.1",         # Xerox Total Impressions
    "1.3.6.1.4.1.1602.1.11.1.3.0"        # Canon Device ID Block (not pure counter, but sometimes works)
]

# ======================================================
# SERIAL NUMBER OIDS (Canon + Xerox + fallback)
# ======================================================
SERIAL_OIDS = [
    "1.3.6.1.4.1.1602.1.11.1.3.0",  # Canon Device ID block (contains SERIAL=XXXX)
    "1.3.6.1.2.1.43.5.1.1.17.1",    # Canon pure serial
    "1.3.6.1.2.1.43.5.1.1.2.1",     # Generic printer serial
    "1.3.6.1.4.1.253.8.53.13.2.1.3.1",  # Xerox serial
]


# ======================================================
# LOW-LEVEL SNMP GET
# ======================================================
def snmp_get_one(ip, oid, community=SNMP_COMMUNITY, timeout=SNMP_TIMEOUT, retries=SNMP_RETRIES):
    try:
        iterator = getCmd(
            SnmpEngine(),
            CommunityData(community, mpModel=0),
            UdpTransportTarget((ip, 161), timeout=timeout, retries=retries),
            ContextData(),
            ObjectType(ObjectIdentity(oid))
        )
        errInd, errStatus, errIndex, varBinds = next(iterator)
        if errInd or errStatus:
            return None

        for vb in varBinds:
            val = vb[1]
            # return as string always
            try:
                return str(val)
            except Exception:
                return None
    except Exception:
        return None


# ======================================================
# COUNTER READER (unchanged)
# ======================================================
def probe_and_read_counter(ip):
    for oid in OID_CANDIDATES:
        v = snmp_get_one(ip, oid)
        if v is None:
            continue
        try:
            return int(v)
        except:
            continue
    return None


# ======================================================
# SERIAL NUMBER READER (NEW)
# ======================================================
def parse_canon_device_id_block(text: str):
    """
    Canon returns something like:
    "DEVICEID:MFG:Canon;MDL:iR3045;CMD:PJL,HTTP;CLS:Printer;DES:Canon...;SERIAL=JXY01234;"
    We extract SERIAL=XXXXX
    """
    if "SERIAL" in text.upper():
        parts = text.replace(";", "; ").split(";")
        for p in parts:
            if "SERIAL" in p.upper():
                serial = p.split("=")[-1].strip()
                if serial:
                    return serial
    return None


def probe_and_read_serial(ip):
    for oid in SERIAL_OIDS:
        raw = snmp_get_one(ip, oid)
        if not raw:
            continue

        raw_str = str(raw).strip()

        # 1) Canon device ID block
        if "CMD:" in raw_str or "SERIAL" in raw_str.upper() or "DEVICEID" in raw_str.upper():
            s = parse_canon_device_id_block(raw_str)
            if s:
                return s

        # 2) If string looks like pure serial (alphanumeric)
        if raw_str and len(raw_str) >= 4:  # avoid trash responses
            return raw_str

    return None
