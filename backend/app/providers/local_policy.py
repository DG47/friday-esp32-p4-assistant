"""Local-only model boundary. No discovery, credentials, downloads or inference."""
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


LOCAL_FAILURE_MESSAGES = {
    'local_model_missing': 'Local reasoning is unavailable: the configured model is not installed. Local commands still work.',
    'local_server_unavailable': 'Local reasoning is unavailable: Ollama is not reachable. Local commands still work.',
    'cloud_disable_required': "Local reasoning is blocked until Ollama's cloud features are disabled. Local commands still work.",
    'cloud_status_unverified': "Local reasoning is blocked because Ollama's cloud-disabled status could not be verified. Local commands still work.",
    'remote_model_rejected': 'Local-only reasoning blocked a nonlocal model. Local commands still work.',
    'remote_response_rejected': 'Local-only reasoning blocked a nonlocal response. Local commands still work.',
    'local_model_capabilities_unverified': "The local model's tool support could not be verified. Local commands still work.",
    'local_model_unavailable': 'The configured local model is unavailable. Local commands still work.',
    'local_metadata_invalid': 'Local model availability could not be verified. Local commands still work.',
    'local_response_invalid': 'The local model returned an invalid response. Please try again.',
    'local_response_empty': 'The local model returned no answer. Please try again.',
    'local_inference_failed': 'Local reasoning could not finish that request. Please try again when the local model is available.',
}


class LocalModelUnavailable(RuntimeError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__('Local-only reasoning unavailable: ' + reason)


def local_endpoint(base_url):
    try:
        if (not isinstance(base_url, str) or any(c.isspace() or ord(c) < 32 for c in base_url)
                or '?' in base_url or '#' in base_url):
            raise ValueError()
        url = urlsplit(base_url)
        host = url.hostname
        if (url.scheme not in {'http', 'https'} or not host or url.username is not None
                or url.password is not None or url.path not in {'', '/'}
                or url.query or url.fragment or '%' in host):
            raise ValueError()
        # Canonicalize localhost to a literal, never trust DNS/proxy resolution.
        if host == 'localhost':
            host = '127.0.0.1'
        else:
            address = ipaddress.ip_address(host)
            if not address.is_loopback or getattr(address, 'ipv4_mapped', None) is not None:
                raise ValueError()
            host = str(address)
        port = url.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
        netloc = ('[' + host + ']' if ':' in host else host) + (':' + str(port) if port else '')
        return urlunsplit((url.scheme, netloc, '', '', ''))
    except (ValueError, TypeError):
        raise ValueError('Local-only requires a literal loopback Ollama URL without credentials or URL extras') from None


def local_model(model):
    if (not isinstance(model, str) or len(model) > 160
            or not re.fullmatch(r'(?:[A-Za-z0-9][A-Za-z0-9_.-]*/)?[A-Za-z0-9][A-Za-z0-9_.-]*(?::[A-Za-z0-9][A-Za-z0-9_.-]*)?', model)
            or 'cloud' in model.lower()):
        raise ValueError('Local-only requires an explicit local model name, not a cloud model or URL')
    return model
