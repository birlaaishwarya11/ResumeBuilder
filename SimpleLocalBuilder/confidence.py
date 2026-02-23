"""
Confidence scoring for parsed resume data.
Analyzes parser output and scores each field on completeness and plausibility.
Returns scores that the frontend uses to highlight fields needing review.
"""

import re

_EMAIL_RE = re.compile(r'^[\w.+-]+@[\w-]+\.[\w.-]+$')
_URL_RE = re.compile(r'^https?://')
_LOCATION_RE = re.compile(r'[A-Z][a-z]+.*,\s*[A-Z]')

# Sections most resumes should have
_EXPECTED_SECTIONS = {'education', 'experience'}


def score_parsed_resume(parsed_data):
    """Compute confidence scores for parsed resume data.

    Returns:
        dict with:
        - overall: float 0.0-1.0
        - fields: {field_name: {score, status, reason?, entries?, missing_fields?}}
        - missing: [field names that are empty]
        - low_confidence: [field names with score < 0.6]
    """
    if not parsed_data or not isinstance(parsed_data, dict):
        return {'overall': 0.0, 'fields': {}, 'missing': ['all'], 'low_confidence': []}

    fields = {}
    missing = []
    low_confidence = []

    # --- Name ---
    name = parsed_data.get('name', '').strip()
    if not name:
        fields['name'] = {'score': 0.0, 'status': 'missing'}
        missing.append('name')
    elif len(name.split()) < 2:
        fields['name'] = {'score': 0.5, 'status': 'needs_review',
                          'reason': 'Single word — may be incomplete'}
        low_confidence.append('name')
    else:
        fields['name'] = {'score': 1.0, 'status': 'complete'}

    # --- Contact ---
    contact = parsed_data.get('contact', {})
    if not contact:
        for ck in ('email', 'phone', 'location'):
            fields[f'contact.{ck}'] = {'score': 0.0, 'status': 'missing'}
            missing.append(f'contact.{ck}')
    else:
        # Email
        email = contact.get('email', '').strip()
        if _EMAIL_RE.match(email):
            fields['contact.email'] = {'score': 1.0, 'status': 'complete'}
        elif email:
            fields['contact.email'] = {'score': 0.4, 'status': 'needs_review',
                                       'reason': f'"{email}" may not be valid'}
            low_confidence.append('contact.email')
        else:
            fields['contact.email'] = {'score': 0.0, 'status': 'missing'}
            missing.append('contact.email')

        # Phone
        phone = contact.get('phone', '').strip()
        digits = re.sub(r'\D', '', phone)
        if len(digits) >= 10:
            fields['contact.phone'] = {'score': 1.0, 'status': 'complete'}
        elif len(digits) >= 7:
            fields['contact.phone'] = {'score': 0.7, 'status': 'complete'}
        elif phone:
            fields['contact.phone'] = {'score': 0.3, 'status': 'needs_review',
                                       'reason': 'Phone number seems short'}
            low_confidence.append('contact.phone')
        else:
            fields['contact.phone'] = {'score': 0.0, 'status': 'missing'}
            missing.append('contact.phone')

        # Location
        location = contact.get('location', '').strip()
        if _LOCATION_RE.search(location):
            fields['contact.location'] = {'score': 1.0, 'status': 'complete'}
        elif location:
            fields['contact.location'] = {'score': 0.6, 'status': 'needs_review',
                                          'reason': 'Location format unclear'}
        else:
            fields['contact.location'] = {'score': 0.0, 'status': 'missing'}
            missing.append('contact.location')

        # LinkedIn (optional)
        linkedin = contact.get('linkedin', '').strip()
        if _URL_RE.match(linkedin) and 'linkedin' in linkedin.lower():
            fields['contact.linkedin'] = {'score': 1.0, 'status': 'complete'}
        elif linkedin:
            fields['contact.linkedin'] = {'score': 0.5, 'status': 'needs_review'}
            low_confidence.append('contact.linkedin')

        # GitHub (optional)
        github = contact.get('github', '').strip()
        if _URL_RE.match(github) and 'github' in github.lower():
            fields['contact.github'] = {'score': 1.0, 'status': 'complete'}
        elif github:
            fields['contact.github'] = {'score': 0.5, 'status': 'needs_review'}
            low_confidence.append('contact.github')

    # --- Content sections ---
    for key, data in parsed_data.items():
        if key in ('name', 'contact'):
            continue

        if data is None or data == '' or data == [] or data == {}:
            fields[key] = {'score': 0.0, 'status': 'empty', 'entries': 0}
            if key in _EXPECTED_SECTIONS:
                missing.append(key)
            continue

        if isinstance(data, str):
            word_count = len(data.split())
            if word_count >= 10:
                fields[key] = {'score': 1.0, 'status': 'complete', 'entries': 1}
            elif word_count >= 3:
                fields[key] = {'score': 0.6, 'status': 'needs_review',
                               'reason': 'Very short text', 'entries': 1}
                low_confidence.append(key)
            else:
                fields[key] = {'score': 0.3, 'status': 'needs_review',
                               'reason': 'Extremely short', 'entries': 1}
                low_confidence.append(key)

        elif isinstance(data, list):
            if not data:
                fields[key] = {'score': 0.0, 'status': 'empty', 'entries': 0}
                if key in _EXPECTED_SECTIONS:
                    missing.append(key)
                continue

            if isinstance(data[0], dict):
                # Entry or skill list — score by field completeness
                entry_scores = []
                all_missing_fields = set()
                for entry in data:
                    filled = 0
                    total = 0
                    for field, val in entry.items():
                        if field.startswith('_'):
                            continue
                        total += 1
                        if val and val != [] and val != '':
                            filled += 1
                        else:
                            all_missing_fields.add(field)
                    entry_scores.append(filled / total if total else 0)

                avg = sum(entry_scores) / len(entry_scores) if entry_scores else 0
                info = {
                    'score': round(avg, 2),
                    'status': 'complete' if avg >= 0.6 else 'needs_review',
                    'entries': len(data),
                }
                if all_missing_fields:
                    info['missing_fields'] = sorted(all_missing_fields)
                fields[key] = info
                if avg < 0.6:
                    low_confidence.append(key)

            elif isinstance(data[0], str):
                fields[key] = {'score': 1.0, 'status': 'complete', 'entries': len(data)}

        elif isinstance(data, dict):
            bullets = data.get('bullets', [])
            if bullets:
                fields[key] = {'score': 1.0, 'status': 'complete', 'entries': len(bullets)}
            else:
                fields[key] = {'score': 0.0, 'status': 'empty', 'entries': 0}
                if key in _EXPECTED_SECTIONS:
                    missing.append(key)

    # Check expected sections that are completely absent from parsed data
    for req in _EXPECTED_SECTIONS:
        if req not in parsed_data and req not in fields:
            fields[req] = {'score': 0.0, 'status': 'missing'}
            missing.append(req)

    # Overall score
    all_scores = [f['score'] for f in fields.values()]
    overall = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

    return {
        'overall': overall,
        'fields': fields,
        'missing': missing,
        'low_confidence': low_confidence,
    }
