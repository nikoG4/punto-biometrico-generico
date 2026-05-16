from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import ipaddress
import socket

from app.config import AppConfig


COMMON_RTSP_PATHS = [
    "",
    "stream1",
    "live",
    "h264",
    "cam/realmonitor?channel=1&subtype=0",
    "Streaming/Channels/101",
]


@dataclass(slots=True)
class VideoSourceCandidate:
    host: str
    port: int
    urls: list[str]


class VideoSourceDiscovery:
    def __init__(self, config: AppConfig):
        self.config = config

    def discover(self) -> list[VideoSourceCandidate]:
        if not self.config.video_discovery.enabled:
            return []
        subnet = self._resolve_subnet()
        if subnet is None:
            return []
        hosts = [str(host) for host in subnet.hosts()]
        ports = [int(port) for port in self.config.video_discovery.ports]
        candidates: list[VideoSourceCandidate] = []
        max_workers = max(1, min(256, self.config.video_discovery.max_workers))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._is_open, host, port): (host, port)
                for host in hosts
                for port in ports
            }
            for future in as_completed(futures):
                host, port = futures[future]
                if future.result():
                    candidates.append(VideoSourceCandidate(host=host, port=port, urls=self._urls_for(host, port)))
        return sorted(candidates, key=lambda item: (item.host, item.port))

    def _resolve_subnet(self) -> ipaddress.IPv4Network | None:
        configured = self.config.video_discovery.subnet.strip().lower()
        if configured and configured != "auto":
            try:
                return ipaddress.ip_network(configured, strict=False)
            except ValueError:
                return None
        ip_address = self._local_ip()
        if not ip_address:
            return None
        parts = ip_address.split(".")
        if len(parts) != 4:
            return None
        return ipaddress.ip_network(".".join(parts[:3]) + ".0/24", strict=False)

    def _local_ip(self) -> str | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                return sock.getsockname()[0]
        except OSError:
            return None

    def _is_open(self, host: str, port: int) -> bool:
        timeout = max(0.05, self.config.video_discovery.timeout_ms / 1000)
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _urls_for(self, host: str, port: int) -> list[str]:
        prefix = f"rtsp://{host}:{port}"
        urls = []
        for path in COMMON_RTSP_PATHS:
            urls.append(prefix + (f"/{path}" if path else "/"))
        return urls
