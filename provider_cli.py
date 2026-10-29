"""CLI presentation for the shared provider configuration service."""
import argparse
from dataclasses import asdict
import getpass
import json
import warnings
from time import perf_counter

from provider_configuration import ProviderConfig, ProviderConfigurationService, ConfigurationError, KEY_NAMES

GUIDANCE = {
    'openai': 'Create an API key at https://platform.openai.com/api-keys . ChatGPT subscriptions do not include API billing.',
    'gemini': 'Create a Gemini API key in Google AI Studio: https://aistudio.google.com/apikey .',
    'openrouter': 'Create an OpenRouter key at https://openrouter.ai/settings/keys . Use a provider/model identifier.',
}


def _choice(prompt, choices):
    while True:
        answer = input(prompt).strip()
        if answer in choices:
            return answer
        print('Please choose ' + ' or '.join(choices) + '.')


def configure_provider(service=None):
    """Return the tested provider on success, or None without replacing active state."""
    service = ProviderConfigurationService() if service is None else service
    try:
        print("Where should Athena's intelligence run?")
        mode = _choice('1. Local AI\n2. Cloud API\nChoice: ', ('1', '2'))
        if mode == '1':
            from settings import DEFAULT_OLLAMA_BASE_URL
            endpoint = input(f'Local Ollama endpoint [{DEFAULT_OLLAMA_BASE_URL}]: ').strip() or DEFAULT_OLLAMA_BASE_URL
            discover = _choice('Query this endpoint for installed models? 1. Yes 2. Manual entry\nChoice: ', ('1', '2'))
            if discover == '1':
                available = service.list_local_models(endpoint, allow_network=True)
                if isinstance(available, tuple):
                    print('Available models: ' + (', '.join(available) if available else '(none installed)'))
                else:
                    print(available.message)
            model = input('Installed model identifier: ').strip()
            config = ProviderConfig('local', model, endpoint)
            credential = None
            acknowledge = False
            print('Web search is not available in local Ollama mode yet.')
        else:
            provider = {'1': 'openai', '2': 'gemini', '3': 'openrouter'}[_choice(
                'Cloud provider:\n1. OpenAI\n2. Google Gemini\n3. OpenRouter\nChoice: ', ('1', '2', '3'))]
            print(GUIDANCE[provider])
            print('Cloud inference sends task context, including supplied history and retrieved evidence, '
                  'to the selected provider. The test sends only synthetic text and may incur API charges.')
            acknowledge = _choice('Acknowledge cloud data transmission and possible charges? 1. Yes 2. Cancel\nChoice: ', ('1', '2')) == '1'
            if not acknowledge:
                print('Provider change cancelled. Previous configuration remains active.')
                return None
            reference = input(f'Credential environment variable [{KEY_NAMES[provider]}]: ').strip() or KEY_NAMES[provider]
            model = input('Model identifier (required): ').strip()
            config = service.validate(ProviderConfig(provider, model, credential_env=reference))
            print('Credentials are never saved to disk. Set this environment variable securely before future launches.')
            with warnings.catch_warnings():
                warnings.simplefilter('error', getpass.GetPassWarning)
                credential = getpass.getpass('API key for this process only (blank uses configured environment): ').strip() or None
        config = service.validate(config)
        if _choice('Run connection/action-protocol test and save only if successful? 1. Yes 2. Cancel\nChoice: ', ('1', '2')) != '1':
            print('Provider change cancelled. Previous configuration remains active.')
            return None
        result = service.switch(config, credential=credential, allow_network=True, acknowledge_cloud=acknowledge)
        print(result.message)
        if result.ok:
            print('Setup complete. Provider configuration saved without credentials.')
            return result.provider
    except (EOFError, KeyboardInterrupt):
        print('\nSetup cancelled. Previous configuration remains active.')
    except getpass.GetPassWarning:
        print('Hidden key input is unavailable. Use a private interactive terminal or a credential environment variable.')
    except (ConfigurationError, OSError, UnicodeError):
        print('Configuration was not saved. Check provider fields, endpoint and file access.')
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description='Athena provider configuration')
    commands = parser.add_subparsers(dest='command')
    config = commands.add_parser('config')
    config.add_argument('setting', choices=('provider', 'show', 'smoke-test'))
    config.add_argument('--run-live', action='store_true', help='Explicitly permit a minimal live request')
    config.add_argument('--acknowledge-cloud', action='store_true', help='Acknowledge cloud transmission and possible charges')
    args = parser.parse_args(argv)
    if args.command is None:
        from agent import main as run
        run()
        return 0
    import runtime_paths
    try:
        runtime_paths.load_configuration()
        service = ProviderConfigurationService()
        if args.setting == 'smoke-test':
            selected = service.read()
            start = perf_counter()
            result = service.test(selected, allow_network=args.run_live,
                                  acknowledge_cloud=args.acknowledge_cloud)
            from tools import redact_secrets
            print(redact_secrets(json.dumps({'provider': selected.provider, 'model': selected.model,
                'ok': result.ok, 'code': result.code, 'message': result.message,
                'elapsed_ms': round((perf_counter()-start)*1000, 1)})))
            return 0 if result.ok else 1
        if args.setting == 'show':
            from tools import redact_secrets
            display = asdict(service.read())
            display['selection_status'] = service.selection_status()
            print(redact_secrets(json.dumps(display, ensure_ascii=False)))
            return 0
        return 0 if configure_provider(service) is not None else 1
    except (ConfigurationError, OSError, UnicodeError):
        print('Configuration could not be read. Check the configuration file and environment.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
