# core/snmp.py
"""
SNMP Reader with SERIAL NUMBER support for:
- Xerox
- Canon iR / iR ADVANCE
- Ricoh / Kyocera fallback

Works with dashboard auto-serial loader and provides error handling.
"""

from typing import Optional, Dict, List, Any
import threading
import config
from core.logging_setup import setup_logging

logger = setup_logging(__name__)

try:
    from pysnmp.hlapi import (
        SnmpEngine, CommunityData, UdpTransportTarget,
        ContextData, ObjectType, ObjectIdentity, getCmd
    )
    HAS_PYSNMP = True
except Exception:
    SnmpEngine = None
    CommunityData = None
    UdpTransportTarget = None
    ContextData = None
    ObjectType = None
    ObjectIdentity = None
    getCmd = None
    HAS_PYSNMP = False

# SNMP OID Candidates for counter/serial (use config)
OID_CANDIDATES = config.OID_PAGE_COUNTER
SERIAL_OIDS = config.OID_SERIAL_NUMBER

# Thread-local storage for SnmpEngine to avoid expensive re-initialization per call
_local_storage = threading.local()


def _get_engine() -> Optional[Any]:
    """Get thread-local SNMP engine instance."""
    if not HAS_PYSNMP:
        return None
    if not hasattr(_local_storage, "engine"):
        _local_storage.engine = SnmpEngine()
    return _local_storage.engine


def snmp_get_one(
    ip: str,
    oid: str,
    community: str = config.SNMP_COMMUNITY,
    timeout: float = config.SNMP_TIMEOUT,
    retries: int = config.SNMP_RETRIES
) -> Optional[str]:
    """
    Fetch a single SNMP value.
    
    Args:
        ip: Target IP address
        oid: OID to query
        community: SNMP community string
        timeout: Timeout in seconds
        retries: Number of retries
    
    Returns:
        Value as string, or None if failed
    """
    if not HAS_PYSNMP:
        return None

    try:
        iterator = getCmd(
            _get_engine(),
            CommunityData(community, mpModel=0),
            UdpTransportTarget((ip, config.SNMP_PORT), timeout=timeout, retries=retries),
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
    except Exception as e:
        logger.debug(f"SNMP get failed for {ip}:{oid} - {e}")
        return None


# ======================================================
# COUNTER READER
# ======================================================
def probe_and_read_counter(ip: str) -> Optional[int]:
    """
    Read page counter from printer.
    
    Args:
        ip: Target IP address
    
    Returns:
        Counter value or None if unavailable
    """
    for oid in OID_CANDIDATES:
        try:
            v = snmp_get_one(ip, oid)
            if v is None:
                continue
            return int(v)
        except (ValueError, TypeError):
            continue
    return None


# ======================================================
# SERIAL NUMBER READER
# ======================================================
def parse_canon_device_id_block(text: str) -> Optional[str]:
    """
    Parse Canon Device ID block to extract serial number.
    
    Canon returns: "DEVICEID:MFG:Canon;MDL:iR3045;CMD:PJL,HTTP;SERIAL=JXY01234;"
    
    Args:
        text: Device ID block string
    
    Returns:
        Serial number or None
    """
    try:
        if "SERIAL" in text.upper():
            parts = text.replace(";", "; ").split(";")
            for p in parts:
                if "SERIAL" in p.upper():
                    serial = p.split("=")[-1].strip()
                    if serial:
                        return serial
    except Exception as e:
        logger.debug(f"Error parsing Canon device ID: {e}")
    return None


def probe_and_read_serial(ip: str) -> Optional[str]:
    """
    Read serial number from printer.
    
    Args:
        ip: Target IP address
    
    Returns:
        Serial number or None if unavailable
    """
    for oid in SERIAL_OIDS:
        try:
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
        except Exception as e:
            logger.debug(f"Error reading serial from {ip}:{oid} - {e}")
            continue

    return None

# ======================================================
# PRINTER DETAILS READER (TONER, TRAYS, ERRORS)
# ======================================================

# Toner level OIDs
TONER_OIDS = {
    'black_current': '1.3.6.1.2.1.43.11.1.1.9.1.1',
    'black_max': '1.3.6.1.2.1.43.11.1.1.8.1.1',
    'cyan_current': '1.3.6.1.2.1.43.11.1.1.9.1.2',
    'cyan_max': '1.3.6.1.2.1.43.11.1.1.8.1.2',
    'magenta_current': '1.3.6.1.2.1.43.11.1.1.9.1.3',
    'magenta_max': '1.3.6.1.2.1.43.11.1.1.8.1.3',
    'yellow_current': '1.3.6.1.2.1.43.11.1.1.9.1.4',
    'yellow_max': '1.3.6.1.2.1.43.11.1.1.8.1.4',
}

# Paper tray OIDs
TRAY_OIDS = {
    'tray1_status': '1.3.6.1.2.1.43.8.2.1.10.1.1',
    'tray2_status': '1.3.6.1.2.1.43.8.2.1.10.1.2',
    'tray3_status': '1.3.6.1.2.1.43.8.2.1.10.1.3',
    'tray4_status': '1.3.6.1.2.1.43.8.2.1.10.1.4',
    'tray1_name': '1.3.6.1.2.1.43.8.2.1.13.1.1',
    'tray2_name': '1.3.6.1.2.1.43.8.2.1.13.1.2',
    'tray3_name': '1.3.6.1.2.1.43.8.2.1.13.1.3',
    'tray4_name': '1.3.6.1.2.1.43.8.2.1.13.1.4',
}

# Error/Status OIDs
STATUS_OIDS = {
    'device_status': '1.3.6.1.2.1.25.3.2.1.5.1',
    'printer_status': '1.3.6.1.2.1.25.3.5.1.1.1',
    'error_state': '1.3.6.1.2.1.25.3.5.1.2.1',
}


def probe_and_read_toner_levels(ip: str) -> Dict[str, Dict[str, Any]]:
    """
    Fetch toner levels and return dict with color levels.
    
    Args:
        ip: Target IP address
    
    Returns:
        Dictionary with toner information {color: {level: %, status: str}}
    """
    result = {}
    try:
        for key, oid in TONER_OIDS.items():
            val = snmp_get_one(ip, oid, timeout=0.5, retries=0)
            if val is not None:
                try:
                    result[key] = int(val)
                except ValueError:
                    result[key] = val
    except Exception as e:
        logger.debug(f"Error reading toner levels from {ip}: {e}")
    
    # Calculate percentages if we have max values
    toner_info = {}
    for color in ['black', 'cyan', 'magenta', 'yellow']:
        current_key = f'{color}_current'
        max_key = f'{color}_max'
        
        if current_key in result and max_key in result:
            try:
                current = int(result[current_key])
                max_val = int(result[max_key])
                if max_val > 0:
                    percentage = int((current / max_val) * 100)
                    toner_info[color] = {
                        'level': percentage,
                        'status': 'ok' if percentage > 25 else ('low' if percentage > 10 else 'critical')
                    }
            except (ValueError, TypeError, ZeroDivisionError):
                pass
    
    return toner_info if toner_info else {}


def probe_and_read_paper_trays(ip: str) -> Dict[str, Dict[str, Any]]:
    """
    Fetch paper tray status.
    
    Args:
        ip: Target IP address
    
    Returns:
        Dictionary with tray information {trayN: {name, status, sheets}}
    """
    result = {}
    try:
        for i in range(1, 5):
            status_oid = TRAY_OIDS.get(f'tray{i}_status')
            name_oid = TRAY_OIDS.get(f'tray{i}_name')
            
            if status_oid:
                status = snmp_get_one(ip, status_oid, timeout=0.5, retries=0)
                if status and 'Error' not in str(status):
                    try:
                        status_int = int(status)
                        if status_int == 0:
                            tray_status = 'empty'
                        elif status_int > 0 and status_int < 10:
                            tray_status = 'low'
                        else:
                            tray_status = 'ok'
                        
                        # Try to get tray name
                        tray_name = snmp_get_one(ip, name_oid, timeout=0.5, retries=0) if name_oid else None
                        if not tray_name or 'Error' in str(tray_name):
                            tray_name = f'Tray {i}'
                        
                        result[f'tray{i}'] = {
                            'name': str(tray_name),
                            'status': tray_status,
                            'sheets': status_int if status_int > 0 else 0
                        }
                    except (ValueError, TypeError):
                        pass
    except Exception as e:
        logger.debug(f"Error reading paper trays from {ip}: {e}")
    
    return result if result else {}


def _decode_error_bits(error_value: Any) -> List[str]:
    """
    Decode printer error state bits (2 bytes).
    
    Args:
        error_value: Error value as hex, int, or string
    
    Returns:
        List of error messages
    """
    errors = []
    try:
        error_str = str(error_value).strip()
        
        if not error_str or error_str == '0' or error_str == '0x0':
            return errors
        
        # Try to parse as hex or integer
        error_bytes = None
        if error_str.startswith('0x'):
            hex_str = error_str[2:]
            if len(hex_str) % 2:
                hex_str = '0' + hex_str
            error_bytes = bytes.fromhex(hex_str)
        else:
            try:
                error_int = int(error_value)
                if error_int > 0:
                    error_bytes = error_int.to_bytes(2, byteorder='big')
            except (ValueError, TypeError):
                pass
        
        if not error_bytes:
            return errors
        
        # Decode bit positions
        if len(error_bytes) >= 1:
            byte1 = error_bytes[0]
            if byte1 & 0x80: errors.append("Low Paper")
            if byte1 & 0x40: errors.append("No Paper")
            if byte1 & 0x20: errors.append("Low Toner")
            if byte1 & 0x10: errors.append("No Toner")
            if byte1 & 0x08: errors.append("Door Open")
            if byte1 & 0x04: errors.append("Paper Jam")
            if byte1 & 0x02: errors.append("Offline")
            if byte1 & 0x01: errors.append("Service Needed")
        
        if len(error_bytes) >= 2:
            byte2 = error_bytes[1]
            if byte2 & 0x80: errors.append("Media Jam")
            if byte2 & 0x40: errors.append("Paused")
            if byte2 & 0x20: errors.append("Shutdown")
            if byte2 & 0x10: errors.append("Memory Error")
            if byte2 & 0x08: errors.append("Output Full")
            if byte2 & 0x04: errors.append("Output Missing")
    except Exception as e:
        logger.debug(f"Error decoding error bits: {e}")
    
    return errors


def probe_and_read_printer_errors(ip: str) -> List[Dict[str, str]]:
    """
    Fetch printer error/status information.
    
    Args:
        ip: Target IP address
    
    Returns:
        List of alert dictionaries {type, message}
    """
    alerts = []
    
    try:
        # Check device status
        dev_status = snmp_get_one(ip, STATUS_OIDS['device_status'], timeout=0.5, retries=0)
        if dev_status:
            try:
                status_int = int(dev_status)
                status_map = {
                    1: 'Unknown',
                    2: 'Running',
                    3: 'Warning',
                    4: 'Testing',
                    5: 'Down'
                }
                status_text = status_map.get(status_int, f'Code {status_int}')
                if status_int in [1, 3, 5]:  # Flag non-OK statuses
                    alerts.append({'type': 'status', 'message': f'Device: {status_text}'})
            except (ValueError, TypeError):
                pass
        
        # Check printer-specific status
        printer_status = snmp_get_one(ip, STATUS_OIDS['printer_status'], timeout=0.5, retries=0)
        if printer_status:
            try:
                status_int = int(printer_status)
                status_map = {
                    1: 'Other',
                    2: 'Unknown',
                    3: 'Idle',
                    4: 'Printing',
                    5: 'Warmup'
                }
                status_text = status_map.get(status_int, f'Code {status_int}')
                if status_int not in [3, 4]:  # Not printing or idle
                    alerts.append({'type': 'printer_status', 'message': f'Status: {status_text}'})
            except (ValueError, TypeError):
                pass
        
        # Check error state
        error_state = snmp_get_one(ip, STATUS_OIDS['error_state'], timeout=0.5, retries=0)
        if error_state and 'Error' not in str(error_state):
            errors = _decode_error_bits(error_state)
            for error_msg in errors:
                alerts.append({'type': 'error', 'message': error_msg})
    
    except Exception as e:
        logger.debug(f"Error reading printer errors from {ip}: {e}")
    
    return alerts if alerts else []