from api.endpoints.app.ai.activity_controller import ActivityController
from api.endpoints.app.ai.approval_controller import ApprovalController
from api.endpoints.app.ai.audit_controller import AuditController
from api.endpoints.app.ai.chat_controller import ChatController
from api.endpoints.app.ai.event_controller import EventController
from api.endpoints.app.ai.message_controller import MessageController
from api.endpoints.app.ai.schedule_controller import ScheduleController
from api.endpoints.app.ai.state_controller import StateController
from api.endpoints.app.ai.storage_controller import StorageController
from api.endpoints.app.ai.switch_controller import SwitchController


def build_ai_endpoints():
    return {
        # The runtime-facing surface (docs/system/chat-session.md): the cards,
        # the mind, the clock's rows — beside the person's doors.
        "Activity": ActivityController(),
        "Approval": ApprovalController(),
        "State": StateController(),
        "Schedule": ScheduleController(),
        "Audit": AuditController(),
        "Chat": ChatController(),
        "Event": EventController(),
        "Message": MessageController(),
        "Storage": StorageController(),
        "Switch": SwitchController(),
    }
