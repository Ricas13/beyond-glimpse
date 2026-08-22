import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PublicWebSecurityTests(unittest.TestCase):
    def test_injected_runtime_does_not_render_metadata_with_innerhtml(self):
        runtime = (ROOT / "web" / "large-library.js").read_text(encoding="utf-8")
        self.assertNotIn(".innerHTML", runtime)
        self.assertIn("window.__beyondGlimpseMetadataSafe = true", runtime)
        self.assertIn("name.textContent = actor.name || ''", runtime)
        self.assertIn("selected.textContent = genre", runtime)

    def test_nginx_has_public_security_headers(self):
        nginx = (ROOT / "config" / "nginx.conf").read_text(encoding="utf-8")
        required = (
            'server_tokens off;',
            'add_header X-Content-Type-Options "nosniff" always;',
            'add_header X-Frame-Options "DENY" always;',
            'add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
            'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always;',
            'add_header Cross-Origin-Opener-Policy "same-origin" always;',
            'add_header Content-Security-Policy ',
            "object-src 'none'",
            "frame-ancestors 'none'",
            "connect-src 'self'",
        )
        for marker in required:
            self.assertIn(marker, nginx)

    def test_public_data_blocks_private_state_and_dotfiles(self):
        nginx = (ROOT / "config" / "nginx.conf").read_text(encoding="utf-8")
        for marker in ("image-state", "checksums", "catalog", "sync", "^/data/.*/\\."):
            self.assertIn(marker, nginx)

    def test_static_cache_location_does_not_shadow_security_headers(self):
        nginx = (ROOT / "config" / "nginx.conf").read_text(encoding="utf-8")
        static_block = nginx.split("# Set caching for static assets", 1)[1].split("# Never serve", 1)[0]
        directives = [
            line.strip()
            for line in static_block.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertIn("expires 7d;", directives)
        self.assertFalse(any(line.startswith("add_header ") for line in directives))


if __name__ == "__main__":
    unittest.main()
