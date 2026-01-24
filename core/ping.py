
import subprocess, platform, socket
IS_WINDOWS = platform.system().lower().startswith('win')

def ping_ip(ip, timeout_ms=500):
    try:
        # 1. Try fast socket connect to Printer Port (9100) or HTTP (80)
        # This is instant and lightweight compared to spawning ping.exe
        timeout_sec = timeout_ms / 1000.0
        try:
            with socket.create_connection((ip, 9100), timeout=timeout_sec):
                return True
        except (OSError, socket.timeout):
            pass

        # 2. Fallback to ICMP Ping if port 9100 is closed
        creation = 0
        if IS_WINDOWS and hasattr(subprocess, 'CREATE_NO_WINDOW'):
            creation = subprocess.CREATE_NO_WINDOW
        if IS_WINDOWS:
            cmd = ['ping','-n','1','-w', str(timeout_ms), ip]
        else:
            cmd = ['ping','-c','1','-W','1', ip]
        res = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=creation)
        return res.returncode == 0
    except Exception:
        return False
