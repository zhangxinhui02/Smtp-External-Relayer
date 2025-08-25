# Base class
from aiosmtpd.smtp import Envelope

class AdapterBase:
    def __init__(self):
        self.name: str = 'base_adapter'

    def start(self):
        pass

    def send_mail(self, envelope: Envelope) -> str:
        pass

    def stop(self):
        pass
