import socket

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # 获取本地出口IP
        s.connect(("8.8.8.8", 53))
        ip = s.getsockname()[0]
    finally:
        s.close()
    return ip
