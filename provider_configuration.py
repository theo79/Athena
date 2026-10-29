"""One explicit provider configuration API for CLI and future trusted interfaces."""
from dataclasses import asdict, dataclass, field, replace
import ipaddress
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

KEY_NAMES = {'openai': 'OPENAI_API_KEY', 'gemini': 'GEMINI_API_KEY', 'openrouter': 'OPENROUTER_API_KEY'}
PROVIDERS = {'local', *KEY_NAMES}
MESSAGES = {
    'ok': 'Provider connection and Athena text action protocol verified.',
    'invalid_configuration': 'Invalid provider, model, endpoint or credential reference.',
    'missing_credentials': 'The selected credential environment variable is not configured.',
    'invalid_credentials': 'Provider authentication failed. Check the credential and account access.',
    'unreachable': 'Could not reach the configured endpoint.',
    'model_unavailable': 'The selected model is unavailable or not installed.',
    'unsupported_capability': 'The model did not support Athena\'s required text action protocol.',
    'rate_limit': 'Provider rate limit or quota reached.',
    'timeout': 'Provider connection test timed out.',
    'provider_error': 'Provider connection test failed.',
    'cloud_ack_required': 'Explicit acknowledgement of cloud data transmission and possible charges is required.',
    'test_required': 'Explicit connection testing is required before saving a provider.',
    'storage_error': 'Configuration could not be saved; the previous configuration remains active.',
    'configuration_changed': 'Configuration changed during testing; retry with the current configuration.',
}


class ConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderConfig:
    provider: str
    model: str
    endpoint: str | None = None
    credential_env: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    # Observed by a synthetic test, not inferred from a provider/model name.
    text_generation: bool = False
    action_protocol: bool = False
    native_tools: bool | None = None
    structured_outputs: bool | None = None
    streaming: bool = False
    context_limit: int | None = None


@dataclass(frozen=True)
class ProviderTestResult:
    ok: bool
    code: str
    message: str
    capabilities: ProviderCapabilities = field(default_factory=ProviderCapabilities)
    provider: object = field(default=None, repr=False, compare=False)


def _result(code, provider=None):
    return ProviderTestResult(code == 'ok', code, MESSAGES.get(code, MESSAGES['provider_error']),
                              ProviderCapabilities(True, True) if code == 'ok' else ProviderCapabilities(),
                              provider if code == 'ok' else None)


def read_preferences(path):
    try:
        raw = Path(path).read_bytes()
    except FileNotFoundError:
        return {}, None
    try:
        data = json.loads(raw.decode('utf-8'))
        if type(data) is not dict or data.get('provider_config_version', 1) != 1:
            raise ValueError
    except (ValueError, UnicodeError):
        raise ConfigurationError('Unsupported or invalid configuration file; it was not replaced.') from None
    return data, raw


def atomic_preferences(path, changes, *, expected=None, check_expected=False):
    """Merge unrelated settings and atomically replace one secret-free JSON file."""
    path = Path(path)
    data, raw = read_preferences(path)
    if check_expected and raw != expected:
        raise ConfigurationError('configuration_changed')
    data.update(changes)
    encoded = json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                prefix='.settings-', delete=False) as output:
            temporary = Path(output.name)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class ProviderConfigurationService:
    """Single-writer trusted host service. Constructors/read/validate do no network I/O.

    Credentials are environment references or process-only inputs, never file data.
    No memory, identity, permissions, conversation or retrieval state is touched.
    """

    def __init__(self, path=None, *, environ=None, provider_factory=None):
        self._path = Path(path) if path is not None else None
        self.environ = os.environ if environ is None else environ
        self._factory = provider_factory

    @property
    def path(self):
        if self._path is not None:
            return self._path
        import settings
        return settings.preferences_path()

    @staticmethod
    def validate(config):
        if type(config) is not ProviderConfig:
            raise ConfigurationError(MESSAGES['invalid_configuration'])
        provider = 'local' if config.provider == 'ollama' else config.provider
        if (type(provider) is not str or provider not in PROVIDERS
                or type(config.model) is not str
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', config.model)):
            raise ConfigurationError(MESSAGES['invalid_configuration'])
        if provider == 'local':
            if config.credential_env is not None or type(config.endpoint) is not str:
                raise ConfigurationError(MESSAGES['invalid_configuration'])
            try:
                url = urlsplit(config.endpoint)
                host = url.hostname or ''
                if (url.scheme not in ('http', 'https') or not host or url.username is not None
                        or url.password is not None or url.query or url.fragment
                        or any(ord(c) < 33 for c in config.endpoint)):
                    raise ValueError
                url.port  # Validate numeric port without exposing malformed input.
                if host != 'localhost' and not host.endswith('.local'):
                    address = ipaddress.ip_address(host)
                    if not (address.is_loopback or address.is_private) or address.is_unspecified:
                        raise ValueError
            except ValueError:
                raise ConfigurationError(MESSAGES['invalid_configuration']) from None
            return replace(config, provider=provider, endpoint=config.endpoint.rstrip('/'))
        reference = config.credential_env or KEY_NAMES[provider]
        if (config.endpoint is not None or type(reference) is not str
                or not re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}', reference)):
            raise ConfigurationError(MESSAGES['invalid_configuration'])
        return replace(config, provider=provider, credential_env=reference)

    def read(self):
        """Read managed saved selection, otherwise retain legacy environment precedence."""
        import settings
        data, _ = read_preferences(self.path)
        if data.get('provider_selection_source') == 'service':
            provider = data.get('model_provider')
            endpoint = data.get('ollama_base_url') if provider in ('local', 'ollama') else None
            return self.validate(ProviderConfig(provider, data.get('model_name'), endpoint,
                                               data.get('credential_env')))
        saved = data.get('model_provider', 'openrouter')
        provider = self.environ.get('MODEL_PROVIDER', saved)
        paired = provider == saved or (isinstance(provider, str) and isinstance(saved, str) and {provider, saved} <= {'local', 'ollama'})
        model = self.environ.get('MODEL_NAME') or (data.get('model_name') if paired else None)
        model = model or settings.DEFAULT_MODELS.get(provider, '')
        endpoint = None
        reference = None
        if provider in ('local', 'ollama'):
            endpoint = self.environ.get('OLLAMA_BASE_URL') or data.get('ollama_base_url') or settings.DEFAULT_OLLAMA_BASE_URL
        else:
            reference = self.environ.get('MODEL_CREDENTIAL_ENV') or (data.get('credential_env') if paired else None)
        return self.validate(ProviderConfig(provider, model, endpoint, reference))

    def selection_status(self):
        """Explain selection precedence without exposing environment values or keys."""
        data, _ = read_preferences(self.path)
        managed = data.get('provider_selection_source') == 'service'
        config = self.read()
        expected = {'MODEL_PROVIDER': config.provider, 'MODEL_NAME': config.model,
                    'MODEL_CREDENTIAL_ENV': config.credential_env}
        if config.provider == 'local':
            expected['OLLAMA_BASE_URL'] = config.endpoint
        conflicts = [name for name, value in expected.items()
                     if name in self.environ and self.environ[name] != value
                     and not (name == 'MODEL_PROVIDER' and self.environ[name] == 'ollama'
                              and value == 'local')]
        if managed and self.environ is os.environ:
            import runtime_paths
            if getattr(runtime_paths, '_PROVIDER_CONFLICT_PATH', None) == self.path.resolve():
                conflicts = sorted(set(conflicts) | set(runtime_paths.PROVIDER_SELECTION_CONFLICTS))
        return {'policy': 'saved_service_selection' if managed else 'legacy_environment_first',
                'conflicts': conflicts if managed else []}

    def _credential(self, config, supplied):
        if config.provider == 'local':
            return None
        value = supplied if supplied is not None else self.environ.get(config.credential_env, '')
        if type(value) is not str or not value.strip():
            raise ConfigurationError('missing_credentials')
        if any(ord(c) < 32 for c in value):
            raise ConfigurationError('invalid_credentials')
        return value.strip()

    def create_provider(self, config, *, credential=None):
        config = self.validate(config)
        key = self._credential(config, credential)
        if self._factory is not None:
            return self._factory(config, key)
        from models import OpenAIProvider, GeminiProvider, OpenRouterProvider, OllamaProvider
        if config.provider == 'local':
            return OllamaProvider(config.model, base_url=config.endpoint)
        adapter = {'openai': OpenAIProvider, 'gemini': GeminiProvider, 'openrouter': OpenRouterProvider}[config.provider]
        return adapter(config.model, api_key=key)

    def list_local_models(self, endpoint, *, allow_network=False):
        if allow_network is not True:
            return _result('test_required')
        config = self.validate(ProviderConfig('local', 'discovery', endpoint))
        from models import ModelFailure
        try:
            value = self.create_provider(config).list_models()
        except Exception:
            return _result('provider_error')
        if isinstance(value, ModelFailure):
            return _result(value.code if value.code in MESSAGES else 'provider_error')
        if type(value) is not list or any(type(name) is not str or not re.fullmatch(
                r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}', name) for name in value):
            return _result('provider_error')
        return tuple(value)

    def test(self, config, *, credential=None, allow_network=False, acknowledge_cloud=False):
        try:
            config = self.validate(config)
        except (ConfigurationError, TypeError):
            return _result('invalid_configuration')
        if allow_network is not True:
            return _result('test_required')
        if config.provider != 'local' and acknowledge_cloud is not True:
            return _result('cloud_ack_required')
        try:
            provider = self.create_provider(config, credential=credential)
        except ConfigurationError as exc:
            return _result(str(exc) if str(exc) in MESSAGES else 'invalid_configuration')
        except Exception:
            return _result('provider_error')
        from models import ModelFailure
        try:
            if config.provider == 'local':
                available = provider.list_models()
                if isinstance(available, ModelFailure):
                    return _result(available.code if available.code in MESSAGES else 'provider_error')
                names = {n[:-7] if n.endswith(':latest') else n for n in available}
                selected = config.model[:-7] if config.model.endswith(':latest') else config.model
                if selected not in names:
                    return _result('model_unavailable')
            response = provider.generate([{'role': 'user', 'content':
                'Connection test only. Reply with exactly this JSON and no other text: '
                '{"action":"final","answer":"athena-connection-ok"}'}])
            if isinstance(response, ModelFailure):
                return _result(response.code if response.code in MESSAGES else 'provider_error')
            data = json.loads(response)
            if type(data) is not dict or data.get('action') != 'final' or type(data.get('answer')) is not str:
                return _result('unsupported_capability')
        except (ValueError, TypeError):
            return _result('unsupported_capability')
        except Exception:
            # Unexpected provider exceptions may contain keys/headers/body. Never echo them.
            return _result('provider_error')
        return _result('ok', provider)

    def switch(self, config, *, credential=None, allow_network=False, acknowledge_cloud=False):
        """Test proposed selection, then commit file and process selection. No fallback."""
        try:
            config = self.validate(config)
            _, original = read_preferences(self.path)
            key = self._credential(config, credential)
        except ConfigurationError as exc:
            return _result(str(exc) if str(exc) in MESSAGES else 'invalid_configuration')
        except (OSError, TypeError):
            return _result('invalid_configuration')
        result = self.test(config, credential=key, allow_network=allow_network,
                           acknowledge_cloud=acknowledge_cloud)
        if not result.ok:
            return result
        changes = {'provider_config_version': 1, 'provider_selection_source': 'service',
                   'model_provider': config.provider,
                   'model_name': config.model, 'credential_env': config.credential_env}
        if config.provider == 'local':
            changes['ollama_base_url'] = config.endpoint
        try:
            atomic_preferences(self.path, changes, expected=original, check_expected=True)
        except ConfigurationError:
            return _result('configuration_changed')
        except (OSError, ValueError, UnicodeError):
            return _result('storage_error')
        # No process changes before file replacement succeeds. Other credentials/settings stay intact.
        self.environ.update(MODEL_PROVIDER=config.provider, MODEL_NAME=config.model)
        if config.provider == 'local':
            self.environ['OLLAMA_BASE_URL'] = config.endpoint
            self.environ.pop('MODEL_CREDENTIAL_ENV', None)
        else:
            self.environ[config.credential_env] = key
            self.environ['MODEL_CREDENTIAL_ENV'] = config.credential_env
        return result
