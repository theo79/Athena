"""Owner-authenticated local settings server. No provider requests during startup."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import getpass
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
import ipaddress
import json
import os
import platform
from pathlib import Path
import secrets
import socket
import ssl
from time import perf_counter

from provider_configuration import ProviderConfig, ProviderConfigurationService, ConfigurationError
from device_gateway import DeviceError, DeviceGateway

MAX_BODY = 8192
READ_TIMEOUT = 5


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON fields are not allowed.')
        result[key] = value
    return result


def _address(address):
    return f'{address[0]}:{address[1]}'


def _stop_devices(gateway):
    gateway.stop()
    return {'enabled':False,'listen':None}


def network_info():
    host = socket.gethostname()
    try:
        addresses = sorted({item[4][0] for item in socket.getaddrinfo(host, None, socket.AF_INET)})
    except OSError:
        addresses = []
    return {'hostname': host, 'addresses': addresses,
            'reachability': 'Not independently tested. Provider checks run only on explicit request.'}


class SettingsServer(HTTPServer):
    allow_reuse_address = True

    def __init__(self, service, *, owner_token, bind='127.0.0.1', port=8765,
                 allow_lan=False, tls_context=None, local_endpoints=(), network_reader=network_info,
                 device_registry_path=None, device_port=8766):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError('Use a port between 0 and 65535.')
        address = ipaddress.ip_address(bind)
        if address.version != 4 or address.is_unspecified or not (address.is_loopback or address.is_private):
            raise ValueError('Use an explicit loopback or private IPv4 listening address.')
        if not address.is_loopback and (allow_lan is not True or tls_context is None):
            raise ValueError('LAN binding requires explicit permission and an HTTPS certificate.')
        if type(owner_token) is not str or not 32 <= len(owner_token) <= 256 or any(not 33 <= ord(c) <= 126 for c in owner_token):
            raise ValueError('An owner token of at least 32 non-space characters is required.')
        self.service = service
        self._owner_digest = hashlib.sha256(owner_token.encode()).digest()
        self._csrf = secrets.token_urlsafe(32)
        self.scheme = 'https' if tls_context is not None else 'http'
        self.network_reader = network_reader
        self.last_check = None
        if device_registry_path is None:
            configured_path = getattr(service, 'path', None)
            if configured_path is not None:
                device_registry_path = Path(configured_path).with_name('devices.json')
            else:
                import settings
                device_registry_path = settings.preferences_path().with_name('devices.json')
        self.device_gateway = DeviceGateway(device_registry_path, port=device_port)
        self.allowed_endpoints = {'http://localhost:11434', 'http://127.0.0.1:11434'}
        for endpoint in local_endpoints:
            config = service.validate(ProviderConfig('local', 'endpoint-validation', endpoint))
            self.allowed_endpoints.add(config.endpoint)
        try:
            current = service.read()
            if current.provider == 'local':
                self.allowed_endpoints.add(current.endpoint)
        except Exception:
            pass  # The settings service is independent of provider availability/configuration.
        super().__init__((bind, port), SettingsHandler)
        self.allowed_hosts = {f'{host}:{self.server_port}' for host in
                              {'localhost', '127.0.0.1', 'athena.local', bind}}
        if self.server_port == (443 if self.scheme == 'https' else 80):
            self.allowed_hosts |= {host.rsplit(':', 1)[0] for host in self.allowed_hosts}
        if tls_context is not None:
            self.socket = tls_context.wrap_socket(self.socket, server_side=True, do_handshake_on_connect=False)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(READ_TIMEOUT)
        return connection, address

    def handle_error(self, request, client_address):
        pass  # Never send/log request bodies, bearer headers, exceptions or provider payloads.

    def server_close(self):
        self.device_gateway.stop()
        super().server_close()

    @property
    def url(self):
        return f'{self.scheme}://{self.server_address[0]}:{self.server_port}'


class SettingsHandler(BaseHTTPRequestHandler):
    server_version = 'AthenaSettings'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        pass  # Deliberately no request/authentication/provider-body logs.

    def _reply(self, status, value, content_type='application/json'):
        if content_type == 'application/json':
            from tools import redact_secrets
            # Redact values from this service's credential environment as well as process globals.
            text = json.dumps(value, ensure_ascii=True, allow_nan=False)
            for name, secret in self.server.service.environ.items():
                if (name == self.server.service.environ.get('MODEL_CREDENTIAL_ENV') or
                        any(word in name.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD'))):
                    if isinstance(secret, str) and len(secret)>=8:
                        text = text.replace(json.dumps(secret)[1:-1], '[REDACTED]')
            body = redact_secrets(text).encode('utf-8')
        else:
            body = value
        self.send_response(status)
        self.send_header('Content-Type', content_type + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, *, write=False):
        hosts = self.headers.get_all('Host', [])
        if len(hosts)!=1 or hosts[0].lower() not in self.server.allowed_hosts:
            self._reply(403, {'error':'Host is not permitted.'})
            return False
        origins = self.headers.get_all('Origin', [])
        expected = self.server.scheme + '://' + hosts[0].lower()
        if (len(origins)>1 or (origins and origins[0].lower()!=expected)
                or (write and not origins)):
            self._reply(403, {'error':'Origin is not permitted.'})
            return False
        auth = self.headers.get_all('Authorization', [])
        token = auth[0][7:] if len(auth)==1 and auth[0].startswith('Bearer ') else ''
        if not hmac.compare_digest(hashlib.sha256(token.encode()).digest(), self.server._owner_digest):
            self._reply(401, {'error':'Owner authentication is required.'})
            return False
        if write and not hmac.compare_digest(self.headers.get('X-Athena-CSRF', ''), self.server._csrf):
            self._reply(403, {'error':'CSRF validation failed.'})
            return False
        return True

    def _discard_small_request_body(self):
        """Drain only tiny declared POST bodies before closing an error response."""
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1:
            return
        try:
            length = int(lengths[0])
        except ValueError:
            return
        if 0 < length <= MAX_BODY + 1:
            self.rfile.read(length)

    def do_GET(self):
        if self.path in ('/', '/assets/settings.css', '/assets/settings.js'):
            # Public login shell only: active settings/network details require owner authentication.
            hosts = self.headers.get_all('Host', [])
            if len(hosts)!=1 or hosts[0].lower() not in self.server.allowed_hosts:
                self._reply(403, {'error':'Host is not permitted.'})
                return
            name, mime = {'/':('settings.html','text/html'),
                          '/assets/settings.css':('settings.css','text/css'),
                          '/assets/settings.js':('settings.js','text/javascript')}[self.path]
            self._reply(200, files('web_assets').joinpath(name).read_bytes(), mime)
            return
        if not self._guard():
            return
        if self.path != '/api/state':
            self._reply(404, {'error':'Not found.'})
            return
        try:
            config = asdict(self.server.service.read())
            policy = self.server.service.selection_status()
            error = None
        except Exception:
            config, policy, error = None, None, 'No valid configuration is available.'
        try:
            network = self.server.network_reader()
        except Exception:
            network = {'hostname':'Unavailable','addresses':[], 'reachability':'Not tested.'}
        check = self.server.last_check
        if check and check['selection'] != config:
            check = None
        try:
            devices = self.server.device_gateway.status()
            device_error = None
        except DeviceError:
            devices = {'enabled':False,'listen':None,'transport':'loopback_unencrypted','paired':[],'pending':[]}
            device_error = 'Device registry unavailable; it was left unchanged.'
        self._reply(200, {'configuration':config,'selection_status':policy,'configuration_error':error,
            'csrf':self.server._csrf,'last_check':check,'devices':devices,'device_error':device_error,'network':network,
            'listen':self.server.url,'local_endpoints':sorted(self.server.allowed_endpoints),
            'system':{'version':'0.12','os':platform.system()},
            'runtime':'Settings server running. Agent runtime is not monitored.',
            'activation':'Saved changes apply to Athena on its next start. Restart any running agent.'})

    def _handle_device_post(self):
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1:
            self._reply(400, {'error':'A single Content-Length is required.'})
            return
        try:
            length = int(lengths[0])
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._reply(413, {'error':'Request size exceeds the permitted limit.'})
            self._discard_small_request_body()
            return
        if self.headers.get('Content-Type') != 'application/json':
            self._reply(415, {'error':'Use application/json.'})
            return
        try:
            body = self.rfile.read(length)
            if len(body) != length:
                raise ValueError
            payload = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object)
            if type(payload) is not dict:
                raise ValueError
            actions = {
                '/api/devices/start': (set(), lambda: {'enabled':True,'listen':_address(self.server.device_gateway.start())}),
                '/api/devices/stop': (set(), lambda: _stop_devices(self.server.device_gateway)),
                '/api/devices/approve': ({'request_id'}, lambda: self.server.device_gateway.approve_pairing(payload['request_id'])),
                '/api/devices/reject': ({'request_id'}, lambda: self.server.device_gateway.reject_pairing(payload['request_id'])),
                '/api/devices/revoke': ({'device_id'}, lambda: self.server.device_gateway.revoke_device(payload['device_id'])),
                '/api/devices/command': ({'device_id','name','arguments'}, lambda: self.server.device_gateway.send_command(
                    payload['device_id'],payload['name'],payload['arguments'])),
            }
            required, action = actions[self.path]
            if set(payload) != required:
                raise ValueError
            result = action()
        except (ValueError, TypeError, UnicodeError, DeviceError, ConfigurationError):
            self._reply(400, {'error':'Device request was rejected. Check pairing state and allowed command arguments.'})
            return
        except (OSError, TimeoutError):
            self._reply(408, {'error':'Device operation timed out or the connection closed.'})
            return
        self._reply(200, result)

    def do_POST(self):
        if not self._guard(write=True):
            self._discard_small_request_body()
            return
        device_paths = {'/api/devices/start','/api/devices/stop','/api/devices/approve',
                        '/api/devices/reject','/api/devices/revoke','/api/devices/command'}
        if self.path in device_paths:
            self._handle_device_post()
            return
        if self.path not in ('/api/test', '/api/switch', '/api/models'):
            self._reply(404, {'error':'Not found.'})
            return
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths)!=1:
            self._reply(400, {'error':'A single Content-Length is required.'})
            return
        try:
            length = int(lengths[0])
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._reply(413, {'error':'Request size exceeds the permitted limit.'})
            self._discard_small_request_body()
            return
        if self.headers.get('Content-Type') != 'application/json':
            self._reply(415, {'error':'Use application/json.'})
            return
        try:
            body = self.rfile.read(length)
            if len(body)!=length:
                raise ValueError
            payload = json.loads(body.decode('utf-8'))
            if type(payload) is not dict or set(payload)-{'selection','allow_network','acknowledge_cloud'}:
                raise ValueError
            if payload.get('allow_network') is not True:
                raise ValueError
            selection = payload.get('selection')
            if type(selection) is not dict or set(selection)-{'provider','model','endpoint','credential_env'}:
                raise ValueError
            config = self.server.service.validate(ProviderConfig(**selection))
            if config.provider=='local' and config.endpoint not in self.server.allowed_endpoints:
                self._reply(403, {'error':'Endpoint is not enabled by the server owner. Use CLI settings or an explicit server allowlist.'})
                return
        except (ValueError, TypeError, UnicodeError, ConfigurationError):
            self._reply(400, {'error':'Invalid settings request. Credential values are not accepted.'})
            return
        except (OSError, TimeoutError):
            self._reply(408, {'error':'Request body timed out.'})
            return
        start = perf_counter()
        try:
            if self.path == '/api/models':
                if config.provider!='local':
                    raise ConfigurationError('Local endpoint required.')
                models = self.server.service.list_local_models(config.endpoint, allow_network=True)
                if isinstance(models, tuple):
                    self._reply(200, {'models':list(models)})
                    return
                result = models
            else:
                method = self.server.service.switch if self.path=='/api/switch' else self.server.service.test
                result = method(config, allow_network=True,
                                acknowledge_cloud=payload.get('acknowledge_cloud') is True)
            checked = {'ok':result.ok,'code':result.code,'message':result.message,
                       'selection':asdict(config),'elapsed_ms':round((perf_counter()-start)*1000,1),
                       'checked_at':datetime.now(timezone.utc).isoformat()}
            self.server.last_check = checked
            self._reply(200 if result.ok else 422, checked)
        except Exception:
            self._reply(500, {'error':'Settings operation could not be confirmed. Reload saved settings before retrying.'})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8765)
    parser.add_argument('--device-port',type=int,default=8766)
    parser.add_argument('--allow-lan',action='store_true')
    parser.add_argument('--cert')
    parser.add_argument('--key')
    parser.add_argument('--local-endpoint',action='append',default=[])
    args = parser.parse_args(argv)
    server = None
    try:
        token = os.getenv('ATHENA_SETTINGS_TOKEN')
        if not token:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                token = getpass.getpass('Owner access token (at least 32 characters; never logged): ')
        context = None
        if args.cert or args.key:
            if not (args.cert and args.key):
                raise ValueError
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(args.cert,args.key)
        import runtime_paths
        runtime_paths.load_configuration()
        server = SettingsServer(ProviderConfigurationService(), owner_token=token, bind=args.bind,
            port=args.port, allow_lan=args.allow_lan,tls_context=context,local_endpoints=args.local_endpoint,
            device_port=args.device_port)
        print('Athena Settings: '+server.url+' (owner authentication required)')
        print('Press Ctrl+C to stop. No provider connection is tested on startup.')
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError, EOFError, getpass.GetPassWarning):
        print('Settings server could not start. Check binding, port, owner token and TLS configuration.')
        return 1
    finally:
        if server is not None:
            server.server_close()
    return 0


if __name__=='__main__':
    raise SystemExit(main())
