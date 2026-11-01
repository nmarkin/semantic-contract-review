"""Presentation only: one colored identifier cell; resolver values stay intact."""

from enum import Enum
from typing import Iterable

import pandas as pd
from pandas.io.formats.style import Styler

from .presentation import summary_rows
from .resolution_models import ResolvedContract, ResolutionState


class ContractStatus(str, Enum):
    CONFIRMED = 'confirmed'
    PROPOSAL = 'proposal'
    MISSING = 'missing'
    REQUIRES_REVIEW = 'requires_review'


STATUS_COLORS = {
    ContractStatus.CONFIRMED: ('#DFF0E5', '#20583C'),
    ContractStatus.PROPOSAL: ('#FFF0CE', '#795219'),
    ContractStatus.MISSING: ('#E4EAF2', '#41546C'),
    ContractStatus.REQUIRES_REVIEW: ('#F8DFD8', '#873D2F'),
}


def contract_status(contract: ResolvedContract) -> ContractStatus:
    states = {field.state for field in contract.fields}
    if ResolutionState.REQUIRES_REVIEW in states:
        return ContractStatus.REQUIRES_REVIEW
    if ResolutionState.MISSING in states:
        return ContractStatus.MISSING
    if contract.proposals:
        return ContractStatus.PROPOSAL
    return ContractStatus.CONFIRMED


def styled_contracts(contracts: Iterable[ResolvedContract]) -> Styler:
    contracts = tuple(contracts)
    frame = pd.DataFrame(summary_rows(contracts))
    styles = pd.DataFrame('', index=frame.index, columns=frame.columns)
    for index, contract in enumerate(contracts):
        background, foreground = STATUS_COLORS[contract_status(contract)]
        styles.loc[index, 'Номер договора'] = f'background-color: {background}; color: {foreground}'
    # Stable Styler UUID also keeps Streamlit widget identity stable on reruns.
    return frame.style.set_uuid('contracts').apply(lambda _: styles, axis=None)
