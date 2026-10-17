# Settings: what the platform itself is configured with. Its own domain
# because endpoint strings are permission strings, and "may name the
# organization's model" is not a grant about anybody's secrets.

from api.endpoints.app.settings.llm_controller import LlmController
from api.endpoints.app.settings.api_key_controller import ApiKeyController
from api.endpoints.app.settings.memory_controller import MemoryController
from api.endpoints.app.settings.oauth_controller import OauthAppController
from api.endpoints.app.settings.routing_controller import RoutingController
from api.endpoints.app.settings.safety_controller import SafetyController
from api.endpoints.app.settings.guardrail_controller import GuardrailController
from api.endpoints.app.settings.speech_controller import SpeechController
from api.endpoints.app.settings.notifications_controller import NotificationsController
from api.endpoints.app.settings.webhook_controller import (
    OutboundController, WebhookController)


def build_settings_endpoints():
    return {
        "Llm": LlmController(),
        # What the assistant remembers about a person: their own
        # configuration of it, beside the models it thinks with.
        "Memory": MemoryController(),
        # A person's own keys for scripts — the person, with a
        # standing credential instead of a browser.
        "ApiKey": ApiKeyController(),
        # The apps the organization registered with providers, so its
        # members can connect accounts with a click.
        "Oauth": OauthAppController(),
        # How a chat finds the right agent among many: the embedding
        # connection and the numbers, the organization's.
        "Routing": RoutingController(),
        # What agents may do without asking: blocked sites, how often
        # scripts and programs are put to the person, allowed packages.
        "Safety": SafetyController(),
        # The organization's own rules for the assistant, added to its
        # instructions: apart from Safety so that reading and writing
        # them are grants of their own.
        "Guardrail": GuardrailController(),
        # A message spoken instead of typed: the transcription model,
        # and the door the composer sends a recording through.
        "Speech": SpeechController(),
        # How a person is reached when not looking: their devices, and
        # whether an email may follow.
        "Notifications": NotificationsController(),
        # A person's doors in from outside systems, each waking a chat
        # of theirs, and their notifications out to other systems
        # (docs/system/webhooks.md).
        "Webhook": WebhookController(),
        "Outbound": OutboundController(),
    }
