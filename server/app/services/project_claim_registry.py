"""Lossless opt-in wire encoding for downstream claim-only model stages."""
import json
from copy import deepcopy

CONTRACT_VERSION = 'source-registry-v1'
SOURCE_FIELDS = ('source_id', 'source_version', 'path')


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def encode_claims(claims):
    registry, aliases, output = {}, {}, []
    for claim in claims:
        refs = []
        for ref in claim['citations']:
            if 'source_ref' in ref or not all(isinstance(ref.get(k), str) and ref[k] for k in SOURCE_FIELDS):
                raise ValueError('Claim source identity cannot be encoded')
            identity = tuple(ref[k] for k in SOURCE_FIELDS)
            if identity not in aliases:
                alias = 's' + str(len(aliases))
                aliases[identity] = alias
                registry[alias] = dict(zip(SOURCE_FIELDS, identity))
            refs.append({'source_ref': aliases[identity], **deepcopy({k: v for k, v in ref.items() if k not in SOURCE_FIELDS})})
        output.append({**deepcopy({k: v for k, v in claim.items() if k != 'citations'}), 'citations': refs})
    encoded = {'input_contract_version': CONTRACT_VERSION, 'source_registry': registry, 'claims': output}
    if decode_claims(encoded) != claims:
        raise ValueError('Claim encoding was not lossless')
    return encoded


def decode_claims(value):
    if value.get('input_contract_version') != CONTRACT_VERSION:
        raise ValueError('Unknown claim input contract')
    registry = value.get('source_registry')
    if not isinstance(registry, dict) or any(
            not isinstance(identity, dict) or set(identity) != set(SOURCE_FIELDS) or
            not all(isinstance(identity[k], str) and identity[k] for k in SOURCE_FIELDS)
            for identity in registry.values()):
        raise ValueError('Invalid claim source registry')
    output = []
    try:
        for claim in value['claims']:
            refs = []
            for ref in claim['citations']:
                if any(k in ref for k in SOURCE_FIELDS):
                    raise ValueError('Ambiguous inline source identity')
                refs.append({**deepcopy(registry[ref['source_ref']]),
                             **deepcopy({k: v for k, v in ref.items() if k != 'source_ref'})})
            output.append({**deepcopy({k: v for k, v in claim.items() if k != 'citations'}), 'citations': refs})
    except (KeyError, TypeError, AttributeError):
        raise ValueError('Missing or invalid claim source alias') from None
    return output


def group_claims(claims, budget):
    """Count the entire actual wire object, including source registry overhead."""
    groups, current = [], []
    for claim in claims:
        if len(_json(encode_claims([claim]))) > budget:
            raise ValueError('One lossless claim input exceeds the material budget')
        proposed = current + [claim]
        if current and len(_json(encode_claims(proposed))) > budget:
            groups.append(current)
            current = [claim]
        else:
            current = proposed
    if current:
        groups.append(current)
    return groups
