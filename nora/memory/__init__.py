"""The memory area."""
from importlib import import_module

from nora.memory.app.act_scope import act_event as act_event
from nora.memory.app.act_scope import act_pass as act_pass
from nora.memory.app.act_scope import active_act_authority as active_act_authority
from nora.memory.app.act_scope import begin_pass_tool as begin_pass_tool
from nora.memory.app.act_scope import declare_act as declare_act
from nora.memory.app.act_scope import record_interrupted_pass_tools as record_interrupted_pass_tools
from nora.memory.app.act_scope import record_pass_expression as record_pass_expression
from nora.memory.app.act_scope import record_pass_silence as record_pass_silence
from nora.memory.app.act_scope import record_pass_tool as record_pass_tool
from nora.memory.app.act_scope import seed_pass_shown as seed_pass_shown
from nora.memory.app.acts import ActEnvelope as ActEnvelope
from nora.memory.app.acts import ActInvocation as ActInvocation
from nora.memory.app.acts import PassItem as PassItem
from nora.memory.app.delivery_arbitration import DeliveryArbitration as DeliveryArbitration
from nora.memory.app.delivery_arbitration import DeliveryRecheck as DeliveryRecheck
from nora.memory.app.effects import EffectRecorder as EffectRecorder
from nora.memory.domain.acts import Act as Act
from nora.memory.domain.acts import ActAuthority as ActAuthority
from nora.memory.domain.acts import Outcome as Outcome
from nora.memory.domain.admission import Refusal as Refusal
from nora.memory.domain.admission import fields as fields
from nora.memory.domain.admission_ports import AdmissionResult as AdmissionResult
from nora.memory.domain.admission_ports import Unavailable as Unavailable
from nora.memory.domain.admission_proposals import CloseIntention as CloseIntention
from nora.memory.domain.admission_proposals import Occasion as Occasion
from nora.memory.domain.admission_proposals import PassContext as PassContext
from nora.memory.domain.admission_proposals import Retire as Retire
from nora.memory.domain.admission_proposals import Revise as Revise
from nora.memory.domain.authored_output import without_reasoning as without_reasoning
from nora.memory.domain.debrief import DebriefObligation as DebriefObligation
from nora.memory.domain.debrief import ProposalId as ProposalId
from nora.memory.domain.intention_operations import IntentionProposal as IntentionProposal
from nora.memory.domain.intention_operations import limit_to_delivery as limit_to_delivery
from nora.memory.domain.intention_operations import (
    parse_intention_operation as parse_intention_operation,
)
from nora.memory.domain.pass_declarations import PassName as PassName
from nora.memory.domain.pass_inputs import (
    AmbientInput as AmbientInput,
)
from nora.memory.domain.pass_inputs import (
    AnchorItem as AnchorItem,
)
from nora.memory.domain.pass_inputs import AssembledPass as AssembledPass
from nora.memory.domain.pass_inputs import (
    CalledInput as CalledInput,
)
from nora.memory.domain.pass_inputs import (
    DebriefInput as DebriefInput,
)
from nora.memory.domain.pass_inputs import PassInput as PassInput
from nora.memory.domain.pass_inputs import (
    PassScope as PassScope,
)
from nora.memory.domain.pass_inputs import (
    ProactiveInput as ProactiveInput,
)
from nora.memory.domain.pass_inputs import (
    ReviewInput as ReviewInput,
)
from nora.memory.domain.pass_inputs import (
    WakeInput as WakeInput,
)
from nora.memory.domain.registration import ITEM_SOURCES as ITEM_SOURCES
from nora.memory.domain.registration import (
    PROTECTED_ITEM_SOURCES as PROTECTED_ITEM_SOURCES,
)
from nora.memory.domain.retrieval import ReadingRoom as ReadingRoom


def __getattr__(name: str):
    if name == "wire":
        return import_module(".wire", __name__)
    raise AttributeError(name)
