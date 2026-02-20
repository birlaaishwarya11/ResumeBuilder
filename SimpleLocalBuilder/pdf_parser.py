"""
Heuristic PDF resume parser.
Extracts text with font metadata from a PDF and maps it to a structured YAML dict
matching the resume template format. No AI is used.
"""

import re
import pdfplumber


# Common section heading keywords (lowercased) — must match the FULL line (not substring)
SECTION_KEYWORDS = {
    "education": ["education", "academic background", "academic history", "academics"],
    "technical_skills": ["technical skills", "skills", "technologies", "competencies",
                         "tools & technologies", "tools and technologies",
                         "core competencies", "technical proficiencies"],
    "experience": ["experience", "professional experience", "work experience",
                   "employment history", "employment", "work history",
                   "relevant experience", "industry experience"],
    "projects": ["projects", "projects and hackathon highlights", "personal projects",
                 "project highlights", "academic projects", "side projects",
                 "key projects", "selected projects"],
    "extracurricular": ["extracurricular", "extracurricular activities", "volunteer",
                        "leadership", "activities", "leadership & activities",
                        "extracurricular activities /volunteer & research papers",
                        "extracurricular activities / volunteer & research papers",
                        "community involvement", "volunteer experience"],
    "certifications": ["certifications", "certifications & licenses", "licenses",
                       "professional certifications", "certificates",
                       "technical certifications"],
    "publications": ["publications", "research", "research papers", "papers",
                     "journal publications", "conference papers"],
    "awards": ["awards", "awards & honors", "honors", "honors & awards",
               "achievements", "accomplishments"],
    "languages": ["languages", "language proficiency", "language skills"],
    "interests": ["interests", "hobbies", "hobbies & interests", "personal interests"],
    "summary": ["summary", "professional summary", "objective", "career objective",
                "profile", "about me", "about", "executive summary"],
}

# Regex patterns
EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+')
PHONE_RE = re.compile(r'[\+]?[\d\s\-\(\)]{7,15}')
URL_RE = re.compile(r'https?://[^\s,]+')
GITHUB_RE = re.compile(r'github\.com/[\w\-]+', re.IGNORECASE)
LINKEDIN_RE = re.compile(r'linkedin\.com/in/[\w\-]+', re.IGNORECASE)
GPA_RE = re.compile(r'(?:GPA|CGPA)[:\s]*(\d+\.?\d*)', re.IGNORECASE)

# Shared month pattern: matches Jan, Feb, ..., Sep, Sept, September, etc.
_MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
_MONTH_SHORT = r'(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep(?:t)?|Oct|Nov|Dec)'

# Date that may be glued to preceding text (no leading space required)
DATE_RE = re.compile(_MONTH + r'\.?\s*\d{4}', re.IGNORECASE)
DATE_RANGE_RE = re.compile(
    r'(' + _MONTH + r'\.?\s*\d{4})'
    r'\s*[-–—]\s*'
    r'(' + _MONTH + r'\.?\s*\d{4}|[Pp]resent|[Cc]urrent)',
    re.IGNORECASE
)
# Also match condensed ranges like "Aug-Dec 2025"
DATE_RANGE_SHORT_RE = re.compile(
    r'(' + _MONTH_SHORT + r'[a-z]*)'
    r'\s*[-–—]\s*'
    r'(' + _MONTH_SHORT + r'[a-z]*)\s+(\d{4})',
    re.IGNORECASE
)
DEGREE_RE = re.compile(
    r'\b(?:B\.?S\.?|M\.?S\.?|B\.?A\.?|M\.?A\.?|B\.?Tech|M\.?Tech|M\.?Eng|B\.?Eng|Ph\.?D|MBA|MEng|BTech'
    r'|Bachelor\s+of\s+\w+|Master\s+of\s+\w+|Doctor\s+of\s+\w+|Associate\s+of\s+\w+)\b',
    re.IGNORECASE
)
INSTITUTION_RE = re.compile(
    r'\b(?:University|Institute|College|School|Tech|Polytechnic|IST|IIT|NIT|MIT|BITS|SRM|IIIT)\b',
    re.IGNORECASE
)
# Pattern for "City, ST" (US 2-letter state) or "City, Country" at end of a line
# City is 1-2 capitalized words (e.g., "New York", "Pune", "San Francisco")
LOCATION_TAIL_RE = re.compile(
    r'\s+((?:[A-Z][a-z]+\s)?[A-Z][a-z]+,\s*(?:[A-Z]{2}|[A-Z][a-z]+))\s*$'
)
BULLET_CHARS = {'•', '▪', '■', '◦', '►', '‣', '▸'}


def extract_lines_with_meta(pdf_path):
    """Extract text lines with font size and bold info from PDF."""
    lines = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            chars = page.chars
            if not chars:
                continue

            current_line_chars = []
            current_y = None

            sorted_chars = sorted(chars, key=lambda c: (round(c['top'], 1), c['x0']))

            for ch in sorted_chars:
                y = round(ch['top'], 1)
                if current_y is None or abs(y - current_y) > 3:
                    if current_line_chars:
                        lines.append(_build_line(current_line_chars))
                    current_line_chars = [ch]
                    current_y = y
                else:
                    current_line_chars.append(ch)

            if current_line_chars:
                lines.append(_build_line(current_line_chars))

    return lines


def _build_line(chars):
    """Build a line dict from a list of character dicts, inserting spaces for gaps."""
    # Sort by x position
    chars = sorted(chars, key=lambda c: c['x0'])

    # Build text with gap-aware spacing
    parts = []
    for i, ch in enumerate(chars):
        if i > 0:
            # If there's a gap between this char and the previous one, insert a space
            prev_end = chars[i - 1].get('x1', chars[i - 1]['x0'] + 5)
            gap = ch['x0'] - prev_end
            # A gap larger than ~2pt typically means a space between words/columns
            if gap > 2:
                parts.append(' ')
        parts.append(ch['text'])

    text = ''.join(parts).strip()

    sizes = [ch.get('size', 0) for ch in chars if ch['text'].strip()]
    avg_size = sum(sizes) / len(sizes) if sizes else 0
    fonts = [ch.get('fontname', '') for ch in chars if ch['text'].strip()]
    is_bold = any('bold' in f.lower() for f in fonts)
    return {
        'text': text,
        'size': avg_size,
        'bold': is_bold,
    }


def classify_section(heading_text):
    """Match a heading string to a known section key. Requires close match, not substring."""
    lower = heading_text.lower().strip()
    # Strip common decorators
    lower = re.sub(r'[/|&,]+', ' ', lower)
    lower = re.sub(r'\s+', ' ', lower).strip()

    for key, keywords in SECTION_KEYWORDS.items():
        for kw in keywords:
            # Exact match or the heading starts with the keyword
            if lower == kw or lower.startswith(kw):
                return key
    return None


def is_section_heading(line, median_size):
    """
    A line is a section heading if:
    1. It's ALL CAPS with >3 alpha chars AND bold or larger than median, OR
    2. It's bold AND larger than median AND matches a known keyword, OR
    3. It's bold + short (<60 chars) + larger than median + no bullets/digits at start
       (catches unknown section headings not in keyword list).
    Pure keyword matching on normal-sized body text is NOT enough.
    """
    text = line['text'].strip()
    if not text or len(text) < 3:
        return False

    alpha_chars = [c for c in text if c.isalpha()]
    is_all_caps = alpha_chars and all(c.isupper() for c in alpha_chars) and len(alpha_chars) > 3
    is_larger = line['size'] > median_size * 1.05

    # ALL CAPS + (bold or larger) → heading
    if is_all_caps and (line['bold'] or is_larger):
        return True

    # Bold + larger + keyword match → heading
    if line['bold'] and is_larger and classify_section(text) is not None:
        return True

    # Fallback: bold + short + larger + no bullet/digit start → likely a heading
    if line['bold'] and is_larger and len(text) < 60:
        if text[0] not in BULLET_CHARS and not text[0].isdigit() and not text.startswith('- '):
            return True

    return False


def parse_contact(lines, num_lines=5):
    """Extract contact info from the first few lines."""
    contact = {
        "location": "",
        "phone": "",
        "email": "",
        "github": "",
        "linkedin": "",
        "portfolio_label": "Portfolio",
        "portfolio_url": ""
    }
    text_block = ' '.join(l['text'] for l in lines[:num_lines])

    email_match = EMAIL_RE.search(text_block)
    if email_match:
        contact['email'] = email_match.group()

    phone_match = PHONE_RE.search(text_block)
    if phone_match:
        phone = phone_match.group().strip()
        digits = re.sub(r'\D', '', phone)
        if len(digits) >= 7:
            contact['phone'] = phone

    urls = URL_RE.findall(text_block)
    for url in urls:
        if GITHUB_RE.search(url):
            contact['github'] = url.rstrip('/')
        elif LINKEDIN_RE.search(url):
            contact['linkedin'] = url.rstrip('/')
        elif not contact['portfolio_url']:
            contact['portfolio_url'] = url.rstrip('/')

    location_re = re.compile(r'[A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z]{2}\b')
    loc_match = location_re.search(text_block)
    if loc_match:
        contact['location'] = loc_match.group()

    return contact


def parse_bullets(text_lines):
    """Extract bullet points from a list of text strings."""
    bullets = []
    current = []
    for line in text_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Check if this line starts a new bullet
        if stripped[0] in BULLET_CHARS:
            if current:
                bullets.append(' '.join(current))
            current = [stripped[1:].strip()]
        elif stripped.startswith('- '):
            if current:
                bullets.append(' '.join(current))
            current = [stripped[2:].strip()]
        else:
            # Continuation of previous bullet or standalone line
            if current:
                current.append(stripped)
            else:
                current = [stripped]
    if current:
        bullets.append(' '.join(current))
    return bullets


def _split_glued_location(text):
    """
    Split text where a location is glued to preceding text.
    e.g., "Cornell Tech, Cornell UniversityNew York, NY" → ("Cornell Tech, Cornell University", "New York, NY")
    """
    # Look for City, ST pattern glued to preceding word
    match = re.search(r'([a-z])([A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*[A-Z]{2})\b', text)
    if match:
        split_pos = match.start(2)
        return text[:split_pos].strip(), text[split_pos:].strip()

    # Look for City, Country pattern
    match = re.search(r'([a-z])([A-Z][a-z]+,\s*[A-Z][a-z]+)', text)
    if match:
        split_pos = match.start(2)
        return text[:split_pos].strip(), text[split_pos:].strip()

    return text, ""


def _split_glued_date(text):
    """
    Split text where a date is glued to preceding text.
    e.g., "GPA: 3.9May 2026" → ("GPA: 3.9", "May 2026")
    """
    match = re.search(
        r'([a-z0-9.])(' + _MONTH + r'\.?\s*\d{4})',
        text, re.IGNORECASE
    )
    if match:
        split_pos = match.start(2)
        return text[:split_pos].strip(), text[split_pos:].strip()

    # Also handle date ranges glued: "IndiaJuly 2022- May 2025"
    match = re.search(
        r'([a-z])(' + _MONTH + r')',
        text, re.IGNORECASE
    )
    if match:
        split_pos = match.start(2)
        return text[:split_pos].strip(), text[split_pos:].strip()

    return text, ""


def _split_institution_location(text):
    """
    Split an institution line into (institution, location).
    Handles: "Cornell Tech, Cornell University New York, NY"
             "SRM IST Chennai, India"
             "Cornell Tech, Cornell UniversityNew York, NY" (glued)
    """
    # First try glued pattern (no space before city)
    main, loc = _split_glued_location(text)
    if loc:
        return main, loc

    # Try "City, ST" or "City, Country" at end of line
    tail_match = LOCATION_TAIL_RE.search(text)
    if tail_match:
        loc = tail_match.group(1).strip()
        inst = text[:tail_match.start()].strip().rstrip(',').strip()
        return inst, loc

    # Try splitting on multiple spaces / tabs
    parts = re.split(r'\s{2,}|\t+', text, maxsplit=1)
    if len(parts) > 1:
        return parts[0].strip(), parts[1].strip()

    return text, ""


def parse_education_section(text_lines, line_meta=None):
    """Parse education entries from lines of text.
    line_meta is an optional parallel list of dicts with 'bold' key.
    """
    entries = []
    current = None

    for idx, line in enumerate(text_lines):
        stripped = line.strip()
        if not stripped:
            continue

        is_bold_line = line_meta[idx]['bold'] if line_meta and idx < len(line_meta) else False

        # Skip "Relevant Coursework" lines — store on current entry
        if stripped.lower().startswith('relevant coursework'):
            if current:
                current['coursework'] = re.sub(r'^[Rr]elevant\s+[Cc]oursework[:\s]*', '', stripped).strip()
            continue

        # Skip bullet lines — they are honors, activities, etc. attached to the current entry
        is_bullet = stripped[0] in BULLET_CHARS or stripped.startswith('- ')
        if is_bullet:
            # Store as honors/extra info on current entry if present
            bullet_text = stripped.lstrip('•▪■◦►‣▸- ').strip()
            if current and bullet_text:
                existing = current.get('honors', '')
                current['honors'] = (existing + '; ' + bullet_text).lstrip('; ')
            continue

        # Check for institution line: matches INSTITUTION_RE or is bold + no degree
        is_institution = INSTITUTION_RE.search(stripped)
        # Also treat bold non-degree lines as potential institution headers
        if not is_institution and is_bold_line and not DEGREE_RE.search(stripped):
            is_institution = True

        if is_institution and not DEGREE_RE.search(stripped):
            if current:
                entries.append(current)
            current = {"institution": "", "location": "", "degree": "", "gpa": "", "date": ""}

            inst, loc = _split_institution_location(stripped)
            current['institution'] = inst
            current['location'] = loc
            continue

        if current is None:
            current = {"institution": "", "location": "", "degree": "", "gpa": "", "date": ""}

        # Check for degree line (may have GPA and date glued or spaced)
        if DEGREE_RE.search(stripped):
            # Split off glued date first: "MEng in Computer Science, GPA: 3.9May 2026"
            main_text, date_part = _split_glued_date(stripped)
            if date_part:
                current['date'] = date_part

            # Extract GPA
            gpa_match = GPA_RE.search(main_text)
            if gpa_match:
                current['gpa'] = gpa_match.group(1)
                degree_text = main_text[:gpa_match.start()].strip().rstrip(',').strip()
            else:
                degree_text = main_text.strip()

            # If no glued date, look for date in original line
            if not date_part:
                date_match = DATE_RE.search(stripped)
                if date_match:
                    current['date'] = date_match.group()
                    # Remove date from degree text if it was included
                    degree_text = re.sub(re.escape(date_match.group()), '', degree_text).strip().rstrip(',').strip()

            current['degree'] = degree_text
            continue

        # Check for GPA on its own
        gpa_match = GPA_RE.search(stripped)
        if gpa_match:
            current['gpa'] = gpa_match.group(1)

        # Check for date
        date_match = DATE_RE.search(stripped)
        if date_match and not current.get('date'):
            current['date'] = date_match.group()

    if current:
        entries.append(current)
    return entries


def parse_experience_section(text_lines, line_meta=None):
    """Parse experience entries. Looks for company/role + date range patterns.
    Handles multi-line headers where company is on one bold line and
    role + date is on the next line.
    line_meta is optional parallel list of dicts with 'bold' key.
    """
    entries = []
    current = None
    pending_company = None  # Bold line without date, waiting for role+date on next line

    for idx, line in enumerate(text_lines):
        stripped = line.strip()
        if not stripped:
            continue

        is_bold_line = line_meta[idx]['bold'] if line_meta and idx < len(line_meta) else False
        is_bullet = stripped[0] in BULLET_CHARS or stripped.startswith('- ')

        # First, try to unsplit glued dates: "Pune, IndiaJuly 2022- May 2025"
        test_text = stripped
        main_part, date_tail = _split_glued_date(test_text)
        if date_tail:
            test_text = main_part + ' ' + date_tail

        # Check for date range
        date_match = DATE_RANGE_RE.search(test_text)
        # Also try short ranges like "Aug-Dec 2025"
        if not date_match:
            date_match = DATE_RANGE_SHORT_RE.search(test_text)

        # Fallback: single date on a bold, non-bullet line
        if not date_match and is_bold_line and not is_bullet:
            single_date = DATE_RE.search(test_text)
            if single_date:
                date_match = single_date

        if date_match:
            if current and (current.get('company') or current.get('_raw_bullets')):
                current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
                current.pop('_raw_bullets', None)
                entries.append(current)

            date_str = date_match.group()
            header_text = test_text[:date_match.start()].strip().rstrip('|').rstrip(',').strip()

            current = {"company": "", "role": "", "location": "", "date": date_str, "_raw_bullets": []}

            # If we had a pending company line (bold line without date on previous line),
            # use it as the company and this line's header_text as the role
            if pending_company:
                current['company'] = pending_company['company']
                current['location'] = pending_company.get('location', '')
                # The header_text on this date line is typically the role
                if header_text:
                    current['role'] = header_text
                pending_company = None
            else:
                # Parse header_text for company, role, location
                parts = re.split(r'\s*[,|]\s*', header_text)
                parts = [p.strip() for p in parts if p.strip()]
                if len(parts) >= 2:
                    current['company'] = parts[0]
                    current['role'] = parts[1]
                    if len(parts) >= 3:
                        current['location'] = ', '.join(parts[2:])
                elif parts:
                    current['company'] = parts[0]
            continue

        # Detect bold non-bullet lines without dates as potential company headers
        # These are lines like "QUANTIPHI ANALYTICS Mumbai, India" that precede
        # a role+date line
        if is_bold_line and not is_bullet and not date_match:
            # This could be a company header for the next entry
            # Save current entry if any
            if current and (current.get('company') or current.get('_raw_bullets')):
                current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
                current.pop('_raw_bullets', None)
                entries.append(current)
                current = None

            # Try to split location from the company line
            company_text = stripped
            location = ""
            # Try "City, ST" or "City, Country" at end
            loc_match = LOCATION_TAIL_RE.search(company_text)
            if loc_match:
                location = loc_match.group(1).strip()
                company_text = company_text[:loc_match.start()].strip().rstrip(',').strip()
            else:
                # Try multiple-space split
                space_parts = re.split(r'\s{2,}|\t+', company_text, maxsplit=1)
                if len(space_parts) > 1:
                    # Check if second part looks like a location
                    second = space_parts[1].strip()
                    if re.match(r'^[A-Z][a-z]+(?:\s[A-Z][a-z]+)*,\s*(?:[A-Z]{2}|[A-Z][a-z]+)', second):
                        company_text = space_parts[0].strip()
                        location = second

            pending_company = {"company": company_text, "location": location}
            continue

        # If there's a pending company but this line is not a date line or bold line,
        # it might be a non-bold role+date line (common format)
        if pending_company and not is_bullet:
            # Check if this non-bold line has a date (role + date on same line)
            role_date_match = DATE_RANGE_RE.search(test_text)
            if not role_date_match:
                role_date_match = DATE_RANGE_SHORT_RE.search(test_text)
            if not role_date_match:
                role_date_match = DATE_RE.search(test_text)

            if role_date_match:
                if current and (current.get('company') or current.get('_raw_bullets')):
                    current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
                    current.pop('_raw_bullets', None)
                    entries.append(current)

                date_str = role_date_match.group()
                role_text = test_text[:role_date_match.start()].strip().rstrip('|').rstrip(',').strip()

                current = {
                    "company": pending_company['company'],
                    "role": role_text,
                    "location": pending_company.get('location', ''),
                    "date": date_str,
                    "_raw_bullets": []
                }
                pending_company = None
                continue

        # Clear pending_company if we hit a bullet (the bold line was just a sub-heading)
        if pending_company and is_bullet:
            # The pending bold line wasn't a company header; treat it as part of current
            if current is None:
                current = {"company": pending_company['company'], "role": "", "location": pending_company.get('location', ''), "date": "", "_raw_bullets": []}
            pending_company = None

        # Accumulate bullet lines
        if current is not None:
            current.setdefault('_raw_bullets', []).append(stripped)

    # Handle any remaining pending company
    if pending_company:
        if current and (current.get('company') or current.get('_raw_bullets')):
            current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
            current.pop('_raw_bullets', None)
            entries.append(current)
        current = {"company": pending_company['company'], "role": "", "location": pending_company.get('location', ''), "date": "", "bullets": []}

    if current:
        current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
        current.pop('_raw_bullets', None)
        entries.append(current)

    return entries


def parse_skills_section(text_lines):
    """Parse technical skills. Looks for 'Category: skills list' patterns."""
    skills = []
    for line in text_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Strip leading bullet characters
        stripped = stripped.lstrip('•▪■◦►‣▸- ').strip()
        if not stripped:
            continue
        colon_match = re.match(r'^([^:]+):\s*(.+)$', stripped)
        if colon_match:
            category = colon_match.group(1).strip()
            skill_text = colon_match.group(2).strip()
            # If last skill entry has same category "General", merge continuation
            if skills and not colon_match and skills[-1]['category'] == 'General':
                skills[-1]['skills'] += ', ' + stripped
            else:
                skills.append({"category": category, "skills": skill_text})
        else:
            # Continuation of previous skill line or standalone
            if skills:
                skills[-1]['skills'] += ', ' + stripped
            else:
                skills.append({"category": "General", "skills": stripped})
    return skills


def parse_projects_section(text_lines, line_meta=None):
    """
    Parse project entries.
    line_meta is optional list of dicts with 'bold' key, parallel to text_lines.
    """
    entries = []
    current = None

    for idx, line in enumerate(text_lines):
        stripped = line.strip()
        if not stripped:
            continue

        is_bullet = stripped[0] in BULLET_CHARS or stripped.startswith('- ')

        if is_bullet:
            if current is not None:
                current.setdefault('_raw_bullets', []).append(stripped)
            continue

        # Check if this non-bullet line looks like a project header
        # Project headers typically have: name | event | award + date
        # They may be bold or contain pipe separators

        # Try to find a date (possibly glued)
        test_text = stripped
        main_part, date_tail = _split_glued_date(test_text)
        if date_tail:
            test_text = main_part + ' ' + date_tail

        date_match = DATE_RE.search(test_text)
        has_pipes = '|' in stripped
        is_bold_line = line_meta[idx]['bold'] if line_meta and idx < len(line_meta) else False

        # A project header if: has date OR has pipes OR is bold (and not a bullet)
        if date_match or has_pipes or is_bold_line:
            if current:
                current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
                current.pop('_raw_bullets', None)
                entries.append(current)

            date_str = ""
            header_text = test_text

            if date_match:
                date_str = date_match.group()
                header_text = test_text[:date_match.start()].strip().rstrip('|').rstrip(',').strip()

            # Also try short date range in header
            short_range = DATE_RANGE_SHORT_RE.search(test_text)
            if short_range:
                date_str = short_range.group()
                header_text = test_text[:short_range.start()].strip().rstrip('|').rstrip(',').strip()

            current = {"name": "", "event": "", "award": "", "date": date_str, "_raw_bullets": []}

            parts = re.split(r'\s*\|\s*', header_text)
            parts = [p.strip() for p in parts if p.strip()]
            if parts:
                current['name'] = parts[0]
            if len(parts) >= 2:
                current['event'] = parts[1]
            if len(parts) >= 3:
                current['award'] = parts[2]
            continue

        # Non-bullet, non-header continuation line
        if current is not None:
            current.setdefault('_raw_bullets', []).append(stripped)
        else:
            current = {"name": stripped, "event": "", "award": "", "date": "", "_raw_bullets": []}

    if current:
        current['bullets'] = parse_bullets(current.get('_raw_bullets', []))
        current.pop('_raw_bullets', None)
        entries.append(current)

    return entries


def parse_extracurricular_section(text_lines):
    """Parse extracurricular section as a flat list of bullets."""
    bullets = parse_bullets(text_lines)
    return {"bullets": bullets}


def _normalize_section_key(heading_text):
    """Convert a heading string to a clean YAML key.
    e.g., 'AWARDS & HONORS' → 'awards_honors'
    """
    text = heading_text.lower().strip()
    text = re.sub(r'[^a-z0-9\s]', '', text)
    text = re.sub(r'\s+', '_', text).strip('_')
    return text or 'other'


def _smart_parse_section(text_lines, line_meta=None):
    """Infer the best parsing strategy for an unknown section.
    Returns (render_type, parsed_data).
    """
    if not text_lines:
        return 'bullets', []

    non_empty = [l for l in text_lines if l.strip()]
    if not non_empty:
        return 'bullets', []

    # Check if >40% lines have "Category: items" pattern → skills
    colon_count = sum(1 for l in non_empty if re.match(r'^[^:]{2,30}:\s+.+', l.strip()))
    if colon_count / len(non_empty) > 0.4:
        return 'skills', parse_skills_section(text_lines)

    # Check for pipe-separated headers (project-like entries)
    # e.g., "Real-time Fraud Detection | British Bank"
    pipe_count = sum(1 for l in non_empty if '|' in l.strip() and not l.strip()[0] in BULLET_CHARS and not l.strip().startswith('- '))
    has_dates = any(DATE_RE.search(l) or DATE_RANGE_RE.search(l) for l in non_empty)
    has_bold = line_meta and any(m.get('bold', False) for m in line_meta if m.get('text', '').strip())

    if pipe_count >= 1:
        return 'entries', parse_projects_section(text_lines, line_meta=line_meta)

    # Check if lines have date ranges → entries (experience-like)
    date_count = sum(1 for l in non_empty if DATE_RANGE_RE.search(l) or DATE_RANGE_SHORT_RE.search(l) or DATE_RE.search(l))
    if date_count >= 1:
        return 'entries', parse_experience_section(text_lines, line_meta=line_meta)

    # Check for bold non-bullet headers (project/entry-like even without dates/pipes)
    if has_bold:
        bold_non_bullet = sum(1 for i, l in enumerate(non_empty)
                              if line_meta and i < len(line_meta) and line_meta[i].get('bold', False)
                              and l.strip() and l.strip()[0] not in BULLET_CHARS and not l.strip().startswith('- '))
        if bold_non_bullet >= 1:
            return 'entries', parse_projects_section(text_lines, line_meta=line_meta)

    # Default: bullets
    return 'bullets', parse_bullets(text_lines)


# Font name mapping from PDF internal names to CSS-friendly names
_FONT_CSS_MAP = {
    'times': ('"Times New Roman", Times, serif', 'Times New Roman'),
    'arial': ('Arial, sans-serif', 'Arial'),
    'helvetica': ('"Helvetica Neue", Helvetica, sans-serif', 'Helvetica'),
    'georgia': ('Georgia, serif', 'Georgia'),
    'calibri': ('Calibri, "Trebuchet MS", sans-serif', 'Calibri'),
    'garamond': ('Garamond, "EB Garamond", serif', 'Garamond'),
    'cambria': ('Cambria, Georgia, serif', 'Cambria'),
    'courier': ('"Courier New", Courier, monospace', 'Courier New'),
    'palatino': ('"Palatino Linotype", Palatino, serif', 'Palatino'),
    'verdana': ('Verdana, Geneva, sans-serif', 'Verdana'),
    'tahoma': ('Tahoma, Geneva, sans-serif', 'Tahoma'),
    'trebuchet': ('"Trebuchet MS", sans-serif', 'Trebuchet MS'),
}


def extract_style_from_pdf(pdf_path):
    """Extract CSS styling info (font, size, margins) from a resume PDF.
    Returns a dict matching the style options in the editor.
    """
    style = {
        'font_family': '"Times New Roman", Times, serif',
        'font_size': '10pt',
        'line_height': '1.2',
        'margin': '0.5in',
        'accent_color': '#000000',
    }

    try:
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                return style

            page = pdf.pages[0]
            chars = page.chars
            if not chars:
                return style

            # --- Font family: most common font among body text ---
            font_counts = {}
            sizes = []
            for ch in chars:
                if ch['text'].strip():
                    fname = ch.get('fontname', '')
                    size = ch.get('size', 0)
                    sizes.append(size)
                    # Normalize font name: strip bold/italic suffixes
                    base = re.sub(r'[-,]?(Bold|Italic|Regular|Medium|Light|Oblique|Roman|MT|PS).*', '',
                                  fname, flags=re.IGNORECASE).strip()
                    base_lower = base.lower().replace(' ', '')
                    font_counts[base_lower] = font_counts.get(base_lower, 0) + 1

            if font_counts:
                dominant_font = max(font_counts, key=font_counts.get)
                for key, (css_val, _label) in _FONT_CSS_MAP.items():
                    if key in dominant_font or dominant_font in key:
                        style['font_family'] = css_val
                        break

            # --- Font size: median of body text (exclude headings) ---
            if sizes:
                sorted_sizes = sorted(sizes)
                median_size = sorted_sizes[len(sorted_sizes) // 2]
                # Body text = chars near median (within 20%)
                body_sizes = [s for s in sizes if abs(s - median_size) < median_size * 0.2]
                if body_sizes:
                    avg_body = sum(body_sizes) / len(body_sizes)
                    style['font_size'] = f'{avg_body:.1f}pt'

            # --- Margins: infer from page bbox ---
            width = page.width   # in points (72 pt = 1 inch)
            height = page.height
            if chars:
                min_x = min(ch['x0'] for ch in chars if ch['text'].strip())
                max_x = max(ch['x1'] for ch in chars if ch['text'].strip())
                min_y = min(ch['top'] for ch in chars if ch['text'].strip())
                max_y = max(ch['bottom'] for ch in chars if ch['text'].strip())

                left_margin = min_x / 72.0
                right_margin = (width - max_x) / 72.0
                top_margin = min_y / 72.0
                bottom_margin = (height - max_y) / 72.0

                # Use the average of left/right as the margin value
                avg_margin = (left_margin + right_margin) / 2
                # Round to nearest 0.05
                avg_margin = round(avg_margin * 20) / 20
                avg_margin = max(0.2, min(1.5, avg_margin))  # clamp
                style['margin'] = f'{avg_margin:.2f}in'

            # --- Line height: estimate from vertical spacing ---
            if len(sizes) > 10:
                # Build line positions
                line_tops = []
                current_y = None
                for ch in sorted(chars, key=lambda c: (round(c['top'], 1), c['x0'])):
                    y = round(ch['top'], 1)
                    if current_y is None or abs(y - current_y) > 3:
                        line_tops.append(y)
                        current_y = y

                if len(line_tops) > 5:
                    spacings = [line_tops[i+1] - line_tops[i] for i in range(len(line_tops)-1)]
                    # Filter to typical body line spacings (not section gaps)
                    median_spacing = sorted(spacings)[len(spacings) // 2]
                    body_spacings = [s for s in spacings if s < median_spacing * 1.5]
                    if body_spacings:
                        avg_spacing = sum(body_spacings) / len(body_spacings)
                        sorted_sizes_body = sorted(sizes)
                        median_font = sorted_sizes_body[len(sorted_sizes_body) // 2]
                        if median_font > 0:
                            lh = avg_spacing / median_font
                            lh = max(1.0, min(2.0, round(lh * 10) / 10))
                            style['line_height'] = str(lh)

    except Exception as e:
        print(f"Style extraction warning: {e}")

    return style


def parse_resume_pdf(pdf_path):
    """
    Main entry point: parse a resume PDF into a structured dict.
    Returns a dict matching the YAML structure used by the resume template.
    """
    lines = extract_lines_with_meta(pdf_path)
    if not lines:
        return {}

    sizes = [l['size'] for l in lines if l['text'].strip()]
    if not sizes:
        return {}

    max_size = max(sizes)
    median_size = sorted(sizes)[len(sizes) // 2]

    # Name = first line with max size
    name = ""
    name_line_idx = 0
    for i, l in enumerate(lines):
        if l['size'] >= max_size * 0.95 and l['text'].strip():
            name = l['text'].strip()
            name_line_idx = i
            break

    # Contact info from lines near the top (after name, before first section)
    contact = parse_contact(lines[name_line_idx + 1:], num_lines=6)

    # Find section boundaries — using the strict heading detector
    sections = []
    for i, line in enumerate(lines):
        if i <= name_line_idx + 1:
            continue
        if is_section_heading(line, median_size):
            key = classify_section(line['text'])
            if key is None:
                # Unknown section: normalize the heading text into a YAML key
                key = _normalize_section_key(line['text'])
                if not key or key in ('other',):
                    key = "unknown_" + str(i)
            # Avoid duplicate section keys (take the first occurrence)
            if not any(s[0] == key for s in sections):
                sections.append((key, i, line['text'].strip()))

    # Extract text and metadata for each section
    section_texts = {}
    section_meta = {}  # parallel bold info
    section_headings = {}  # original heading text for unknown sections
    for idx, (key, start, heading_text) in enumerate(sections):
        end = sections[idx + 1][1] if idx + 1 < len(sections) else len(lines)
        section_texts[key] = [lines[j]['text'] for j in range(start + 1, end)]
        section_meta[key] = [lines[j] for j in range(start + 1, end)]
        section_headings[key] = heading_text

    # Summary: text between contact and first section
    summary = ""
    if sections:
        first_section_start = sections[0][1]
        summary_lines = []
        for i in range(name_line_idx + 2, first_section_start):
            text = lines[i]['text'].strip()
            if text and not is_section_heading(lines[i], median_size):
                if EMAIL_RE.search(text) or PHONE_RE.search(text):
                    continue
                if URL_RE.search(text) and len(text) < 80:
                    continue
                summary_lines.append(text)
        if summary_lines:
            summary = ' '.join(summary_lines)

    # Build result
    result = {}

    if summary:
        result['summary'] = summary

    if 'education' in section_texts:
        meta = section_meta.get('education', None)
        result['education'] = parse_education_section(section_texts['education'], line_meta=meta)

    if 'technical_skills' in section_texts:
        result['technical_skills'] = parse_skills_section(section_texts['technical_skills'])

    if 'experience' in section_texts:
        meta = section_meta.get('experience', None)
        result['experience'] = parse_experience_section(section_texts['experience'], line_meta=meta)

    if 'projects' in section_texts:
        meta = section_meta.get('projects', None)
        result['projects'] = parse_projects_section(section_texts['projects'], line_meta=meta)

    if 'extracurricular' in section_texts:
        result['extracurricular'] = parse_extracurricular_section(section_texts['extracurricular'])

    if 'certifications' in section_texts:
        result['certifications'] = parse_skills_section(section_texts['certifications'])

    # Handle "summary" if detected as a section
    if 'summary' in section_texts and 'summary' not in result:
        result['summary'] = ' '.join(l.strip() for l in section_texts['summary'] if l.strip())

    # Unknown / custom sections — use smart parsing
    for key, text_lines in section_texts.items():
        if key not in result and not key.startswith('unknown_'):
            render_type, parsed = _smart_parse_section(text_lines, section_meta.get(key))
            result[key] = parsed

    return {"name": name, "contact": contact, **result}
