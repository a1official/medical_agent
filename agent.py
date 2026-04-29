import os
import re
import json
import time
import requests
import sys
from typing import Any, Optional
from dataclasses import dataclass
from urllib.parse import urlparse, quote_plus, urljoin
from datetime import datetime
from bs4 import BeautifulSoup
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

DEFAULT_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

SOURCE_GROUPS = [
    {"id": "guidelines", "label": "Clinical guidelines", "description": "NICE, WHO, CDC, ACC, AHA, IDSA, ACOG, AAFP, AAD, ASCO, KDIGO, GINA", "domains": ["nice.org.uk", "who.int", "cdc.gov", "acc.org", "aha.org", "idsociety.org", "acog.org", "aafp.org", "aad.org", "asco.org", "kdigo.org", "ginasthma.org"]},
    {"id": "consumer", "label": "Patient information", "description": "MedlinePlus and consumer-facing health portals", "domains": ["medlineplus.gov"]},
    {"id": "research", "label": "Medical research", "description": "PubMed, PMC, and peer-reviewed literature", "domains": ["pubmed.ncbi.nlm.nih.gov"]},
    {"id": "drugs", "label": "Drug databases", "description": "FDA, DailyMed, and pharmacological databases", "domains": ["dailymed.nlm.nih.gov"]}
]

@dataclass
class EvidenceItem:
    source_group: str
    title: str
    url: str
    snippet: str

def normalize_host(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host

def allowed_hosts() -> set[str]:
    return {
        "nice.org.uk", "who.int", "cdc.gov", "acc.org", "aha.org",
        "idsociety.org", "acog.org", "aafp.org", "aad.org", "asco.org",
        "kdigo.org", "ginasthma.org", "medlineplus.gov", "pubmed.ncbi.nlm.nih.gov",
        "dailymed.nlm.nih.gov"
    }

def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    # Decompose non-content elements
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer", "header"]):
        tag.decompose()
        
    # Specifically for NICE, try to target the main content
    main = soup.find("main") or soup.find(id="main-content") or soup.find(class_="guideline-content")
    if main:
        soup = main

    for block in soup.find_all(["p", "div", "section", "article", "li", "tr", "td", "th", "br", "hr", "h1", "h2", "h3", "h4", "h5", "h6"]):
        # If the block is a list item or table cell, preserve its structure better
        if block.name in ["li", "td", "th"]:
            block.insert_before(" • ")
        block.insert_before("\n")
    
    text = soup.get_text("\n", strip=True)
    # Clean up repetitive whitespace and too many newlines
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    
    # Filter out navigation-like lines
    lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Skip lines that look like breadcrumbs or menus
        if " > " in line or " | " in line:
            if not any(drug in line.lower() for drug in ["inhibitor", "blocker", "diuretic", "statin", "agonist", "pril", "sartan", "pine", "olol"]):
                continue
        lines.append(line)
        
    return "\n".join(lines)

def fetch_page(url: str) -> Optional[dict[str, str]]:
    try:
        parsed = urlparse(url)
        fragment = parsed.fragment
        
        res = requests.get(url, headers={"user-agent": DEFAULT_UA}, timeout=15)
        res.raise_for_status()
        
        soup = BeautifulSoup(res.text, "html.parser")
        
        # If there's a fragment, try to target that specific section
        if fragment:
            target = soup.find(id=fragment) or soup.find(attrs={"name": fragment})
            if target:
                # If the target is a container, take its whole text
                if target.name in ["section", "div", "article"] or len(target.get_text()) > 300:
                    text = html_to_text(str(target))
                    if len(text) > 500:
                        return {"title": "", "text": text}
                
                # Otherwise (like a header), take siblings
                content = [str(target)]
                for sibling in target.find_next_siblings():
                    # Stop if we hit a new major section or a research section
                    sibling_text = sibling.get_text().lower()
                    if "recommendations for research" in sibling_text or "research recommendations" in sibling_text:
                        break
                    
                    if sibling.name in ["h1", "h2", "h3", "h4"]:
                        target_level = 3
                        if target.name and len(target.name) == 2 and target.name[1].isdigit():
                            target_level = int(target.name[1])
                        
                        sibling_level = int(sibling.name[1])
                        if sibling_level <= target_level:
                            break
                    content.append(str(sibling))
                text = html_to_text("".join(content))
                if len(text) > 100:
                    return {"title": "", "text": text}

        text = html_to_text(res.text)
        return {"title": "", "text": text}
    except Exception:
        return None

def sentence_score(sentence: str, query: str) -> int:
    haystack = sentence.lower()
    terms = [term for term in re.split(r"[^a-z0-9]+", query.lower()) if len(term) > 3]
    score = 0
    for term in terms:
        if term in haystack:
            score += 3
    
    # Priority medical recommendation verbs and markers
    if re.search(r"^(offer|consider|advise|discuss|use|start)\b", haystack):
        score += 10
    if "first-line" in haystack or "first line" in haystack or "step 1" in haystack:
        score += 25 # Increased from 20
    if "step 2" in haystack or "step 3" in haystack or "step 4" in haystack:
        score += 20 # Increased from 15
    if "recommended" in haystack or "recommendation" in haystack:
        score += 5
    if "pharmacological" in haystack or "drug treatment" in haystack:
        score += 10
    if "management" in haystack or "treatment" in haystack:
        score += 4
    if "dosage" in haystack or "dose" in haystack:
        score += 5
    
    # Indicators for disease severity or specific patient groups
    context_indicators = [
        "chronic kidney disease", "ckd", "renal impairment", "low-severity", "moderate-severity",
        "high-severity", "curb65", "curb-65", "pregnancy", "elderly", "children", "infants",
        "first-line", "second-line", "adjunctive", "monotherapy", "combination therapy"
    ]
    for ci in context_indicators:
        if ci in haystack:
            score += 5

    # Penalize research and overview sections
    if "research" in haystack or "recommendations for research" in haystack:
        score -= 25
    if "download guidance (pdf)" in haystack or "published:" in haystack:
        score -= 15
    
    # Penalize research-style questions
    if haystack.startswith("what is the") or haystack.startswith("how can") or haystack.startswith("to what extent"):
        if "?" in haystack or "research" in haystack:
            score -= 20

    # Common drug classes and suffixes to identify treatment sections
    drug_indicators = [
        "inhibitor", "blocker", "diuretic", "statin", "agonist", "antagonist",
        "pril", "sartan", "pine", "olol", "thiazide", "mab", "nib", "corti",
        "steroid", "insulin", "metformin", "amlodipine", "ramipril", "losartan",
        "candesartan", "lisinopril", "atenolol", "nifedipine", "doxazosin",
        "indapamide", "chlortalidone", "furosemide", "spironolactone",
        "beclometasone", "fluticasone", "budesonide", "salbutamol", "formoterol", "salmeterol", "mart",
        "ace inhibitor", "arb", "ccb", "calcium channel blocker", "beta-blocker", "alpha-blocker",
        "sglt2", "dapagliflozin", "empagliflozin", "canagliflozin", "doac", "apixaban", "rivaroxaban",
        "edoxaban", "dabigatran", "warfarin", "amoxicillin", "doxycycline", "clarithromycin",
        "triptan", "sumatriptan", "entresto", "sacubitril", "valsartan", "bisoprolol", "carvedilol",
        "erythromycin", "azithromycin", "co-amoxiclav", "levofloxacin", "moxifloxacin"
    ]
    for dt in drug_indicators:
        if dt in haystack:
            score += 8 # Increased from 5
            
    # Look for common drug name patterns (capitalized words in the middle of sentences)
    if re.search(r"\b[A-Z][a-z]{3,}\b", sentence):
        score += 2
        
    return score

def summarize_for_evidence(text: str, query: str, limit: int = 5000) -> str:
    # Basic extraction of most relevant sentences
    sentences = re.split(r"(?<=[.!?])\s+", text)
    scored = []
    for s in sentences:
        s = s.strip()
        if len(s) < 15: continue
        scored.append((sentence_score(s, query), s))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    
    selected = []
    length = 0
    # Take more top sentences to provide better context for the LLM
    top_sentences = [s for score, s in scored[:50] if score > 5]
    
    # Sort top sentences by their original appearance in text
    final_sentences = []
    for s in sentences:
        if s in top_sentences and s not in final_sentences:
            final_sentences.append(s)
            length += len(s)
            if length > limit: break
            
    return "\n".join(final_sentences)

def normalize_query_terms(query: str) -> list[str]:
    # Extract core medical terms from query
    STOPWORDS = {
        "what", "is", "the", "for", "how", "to", "treat", "of", "in", "a", "an",
        "are", "does", "do", "can", "you", "give", "show", "me", "find", "search",
        "about", "on", "with", "from", "at", "it", "this", "that"
    }
    
    words = [w for w in re.split(r"[^a-z0-9\-]+", query.lower()) if w and w not in STOPWORDS]
    if not words:
        return [query]
    
    # Create a few variants
    variants = [query]
    
    # Add common medical synonyms and guideline codes
    synonyms = {
        "hypertension": ["blood pressure", "bp", "ng136", "ace inhibitor", "arb", "ccb", "amlodipine", "ramipril"],
        "asthma": ["respiratory", "inhaler", "ng245", "mart", "ics", "formoterol", "beclometasone"],
        "diabetes": ["metformin", "sglt2", "dapagliflozin", "empagliflozin", "ckd", "renal", "ng28"],
        "pneumonia": ["cap", "antibiotic", "amoxicillin", "doxycycline", "clarithromycin", "curb65", "ng138"],
        "heart failure": ["hfref", "hfpef", "entresto", "mra", "sglt2", "ng106"],
        "atrial fibrillation": ["af", "anticoagulation", "doac", "apixaban", "rivaroxaban", "ng196"],
        "acute coronary syndromes": ["acs", "stemi", "nstemi", "myocardial infarction", "unstable angina", "ng185"],
        "migraine": ["headache", "triptan", "sumatriptan", "cg150"],
        "medicine": ["drug", "medication", "treatment", "pharmacological", "therapy"],
        "first-line": ["step 1", "initial", "starting"]
    }
    
    for word in words:
        if word in synonyms:
            for syn in synonyms[word]:
                variants.append(syn)
    
    if len(words) > 1:
        variants.append(" ".join(words))
    
    # If it's a "medicine for X" query, make sure X is a variant
    medical_intent = {"medicine", "medication", "treatment", "drug", "cure", "remedy", "first-line", "management"}
    if any(w in medical_intent for w in words):
        disease_words = [w for w in words if w not in medical_intent]
        if disease_words:
            disease_query = " ".join(disease_words)
            variants.append(disease_query)
            # Add pharmacological variants
            variants.append(f"{disease_query} pharmacological management")
            variants.append(f"{disease_query} drug treatment")
            
    return list(dict.fromkeys(variants))

def result_matches_query(query: str, title: str, url: str) -> bool:
    q = query.lower()
    t = title.lower()
    u = url.lower()
    
    # If the URL is a NICE guidance subpage, it might be very relevant even if title is short
    if "nice.org.uk/guidance/" in u:
        # High-intent guideline sections
        if any(term in t or term in u for term in ["recommendation", "management", "treatment", "pharmacological", "pathway"]):
            return True
            
    terms = [w for w in re.split(r"[^a-z0-9]+", q) if len(w) > 3]
    if not terms:
        return True
    
    match_count = 0
    for term in terms:
        if term in t or term in u:
            match_count += 1
            
    # At least one core term should match
    return match_count > 0

def search_nice(query: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    
    q_lower = query.lower()
    # High-priority direct guideline mapping
    direct_mapping = [
        # Hypertension
        (re.compile(r"hypertension|blood pressure"), [
            ("https://www.nice.org.uk/guidance/ng136/chapter/recommendations#choosing-antihypertensive-drug-treatment-step-1-to-step-4", "NG136 Choosing Treatment (Hypertension)"),
            ("https://www.nice.org.uk/guidance/ng136/chapter/pharmacological-management", "NG136 Pharmacological Management (Hypertension)"),
            ("https://www.nice.org.uk/guidance/ng136/chapter/recommendations", "NG136 Recommendations (Hypertension)")
        ]),
        # Asthma
        (re.compile(r"asthma"), [
            ("https://www.nice.org.uk/guidance/ng245/chapter/recommendations", "NG245 Recommendations (Asthma)"),
            ("https://www.nice.org.uk/guidance/ng245/chapter/pharmacological-management-in-people-aged-12-and-over", "NG245 Pharmacological Management (Asthma)")
        ]),
        # Diabetes
        (re.compile(r"diabetes|metformin|sglt2"), [
            ("https://www.nice.org.uk/guidance/ng28/chapter/recommendations#sglt2-inhibitors-for-people-with-type-2-diabetes-and-chronic-kidney-disease", "NG28 SGLT2i and CKD (Diabetes)"),
            ("https://www.nice.org.uk/guidance/ng28/chapter/recommendations#drug-treatment", "NG28 Drug Treatment (Type 2 Diabetes)"),
            ("https://www.nice.org.uk/guidance/ng28/chapter/recommendations#pharmacological-treatments", "NG28 Pharmacological Treatments (Diabetes)")
        ]),
        # Heart Failure
        (re.compile(r"heart failure|hfref|hfpef"), [
            ("https://www.nice.org.uk/guidance/ng106/chapter/recommendations#pharmacological-treatment-for-heart-failure-with-reduced-ejection-fraction", "NG106 Pharmacological Treatment (HFrEF)"),
            ("https://www.nice.org.uk/guidance/ng106/chapter/recommendations", "NG106 Recommendations (Heart Failure)")
        ]),
        # Atrial Fibrillation
        (re.compile(r"atrial fibrillation|anticoagulation|chads"), [
            ("https://www.nice.org.uk/guidance/ng196/chapter/recommendations#stroke-prevention", "NG196 Stroke Prevention (Atrial Fibrillation)"),
            ("https://www.nice.org.uk/guidance/ng196/chapter/recommendations", "NG196 Recommendations (Atrial Fibrillation)")
        ]),
        # Pneumonia
        (re.compile(r"pneumonia|antibiotic"), [
            ("https://www.nice.org.uk/guidance/ng250/chapter/recommendations#pharmacological-management", "NG250 Pharmacological Management (Pneumonia)"),
            ("https://www.nice.org.uk/guidance/ng250/chapter/recommendations#choice-of-antibiotic", "NG250 Antibiotic Choice (Pneumonia)"),
            ("https://www.nice.org.uk/guidance/ng250/chapter/recommendations", "NG250 Recommendations (Pneumonia)")
        ]),
        # Acute Coronary Syndromes
        (re.compile(r"acs|stemi|nstemi|myocardial infarction|unstable angina"), [
            ("https://www.nice.org.uk/guidance/ng185/chapter/recommendations#drug-therapy-to-prevent-further-cardiovascular-events", "NG185 Drug Therapy (ACS)"),
            ("https://www.nice.org.uk/guidance/ng185/chapter/recommendations", "NG185 Recommendations (ACS)")
        ]),
        # Migraine
        (re.compile(r"migraine|triptan"), [
            ("https://www.nice.org.uk/guidance/cg150/chapter/recommendations#management", "CG150 Management (Migraine)"),
            ("https://www.nice.org.uk/guidance/cg150/chapter/recommendations", "CG150 Recommendations (Headaches)")
        ])
    ]

    for pattern, urls in direct_mapping:
        if pattern.search(q_lower):
            for url, title in urls:
                if url not in seen:
                    seen.add(url)
                    results.append({"url": url, "title": title, "rank_boost": 95})

    # Expand query for NICE to get more specific sections
    variants = normalize_query_terms(query)
    
    for variant in variants:
        # 1. Search with variants
        url = f"https://www.nice.org.uk/search?q={quote_plus(variant)}"
        try:
            response = requests.get(url, timeout=10, headers={"user-agent": DEFAULT_UA})
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"]
                text = a.get_text(" ", strip=True)
                
                # We want /guidance/ URLs
                if "/guidance/" not in href:
                    continue
                
                full_url = urljoin("https://www.nice.org.uk", href)
                if full_url in seen:
                    continue
                
                # Score based on URL keywords
                boost = 0
                h_lower = href.lower()
                if "/chapter/" in h_lower:
                    boost += 10
                    if any(kw in h_lower for kw in ["pharmacological", "treatment", "medicine", "drug", "recommendation", "management"]):
                        boost += 30
                
                if not result_matches_query(query, text, href) and boost < 20:
                    continue
                
                seen.add(full_url)
                results.append({"url": full_url, "title": text, "rank_boost": boost})
                if len(results) >= 20: break
        except Exception: continue

    return results

def search_medline(query: str) -> list[dict[str, str]]:
    results = []
    url = f"https://medlineplus.gov/search?proxystylesheet=medlineplus_frontend&output=xml_no_dtd&q={quote_plus(query)}"
    try:
        response = requests.get(url, timeout=10, headers={"user-agent": DEFAULT_UA})
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "medlineplus.gov" in href and not any(x in href for x in ["/spanish/", "/news/", "/ency/"]):
                results.append({"url": href, "title": a.get_text()})
                if len(results) >= 5: break
    except Exception:
        pass
    return results

def search_pubmed(query: str) -> list[dict[str, str]]:
    results = []
    url = f"https://pubmed.ncbi.nlm.nih.gov/?term={quote_plus(query)}&size=5"
    try:
        response = requests.get(url, timeout=10, headers={"user-agent": DEFAULT_UA})
        soup = BeautifulSoup(response.text, "html.parser")
        for article in soup.find_all("a", class_="docsum-title", href=True):
            href = urljoin("https://pubmed.ncbi.nlm.nih.gov", article["href"])
            results.append({"url": href, "title": article.get_text(strip=True)})
            if len(results) >= 5: break
    except Exception:
        pass
    return results

def search_generic_site(domain: str, query: str) -> list[dict[str, str]]:
    results = []
    search_url = ""
    if "who.int" in domain:
        search_url = f"https://www.who.int/search?query={quote_plus(query)}"
    elif "cdc.gov" in domain:
        search_url = f"https://search.cdc.gov/search/?query={quote_plus(query)}"
    elif "fda.gov" in domain:
        search_url = f"https://search.usa.gov/search?affiliate=fda&query={quote_plus(query)}"
    elif "dailymed.nlm.nih.gov" in domain:
        search_url = f"https://dailymed.nlm.nih.gov/dailymed/search.cfm?query={quote_plus(query)}"
    
    if not search_url:
        return []

    try:
        response = requests.get(search_url, timeout=10, headers={"user-agent": DEFAULT_UA})
        soup = BeautifulSoup(response.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("/"):
                href = urljoin(f"https://{domain}", href)
            if domain in normalize_host(href) and len(a.get_text()) > 20:
                if not any(x in href.lower() for x in ["/search", "/login", "/contact", "/about"]):
                    results.append({"url": href, "title": a.get_text(strip=True)})
                    if len(results) >= 3: break
    except Exception:
        pass
    return results

class TrustedMedicalAgent:
    def __init__(self):
        api_key = os.getenv("NVIDIA_API_KEY")
        model = os.getenv("NVIDIA_CHAT_MODEL", "meta/llama-3.3-70b-instruct")
        if api_key:
            self.client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=api_key)
        else:
            self.client = None
        self.model = model

    def _llm_json(self, messages: list[dict], timeout: int = 60) -> dict:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                timeout=timeout
            )
            content = response.choices[0].message.content
            return json.loads(content)
        except Exception as e:
            print(f"LLM Error: {e}")
            return {"ok": False, "error": str(e)}

    def _route(self, query: str) -> list[dict]:
        q = query.lower()
        selected = [SOURCE_GROUPS[0]]
        if any(w in q for w in ["drug", "dose", "tablet", "pill", "side effect", "metformin", "statin", "pharmacological", "treatment"]):
            selected.append(SOURCE_GROUPS[3])
        if any(w in q for w in ["what is", "symptoms", "overview", "patient", "how to", "risk"]):
            selected.append(SOURCE_GROUPS[1])
        if any(w in q for w in ["trial", "study", "evidence", "research", "pubmed", "efficacy", "safety"]):
            selected.append(SOURCE_GROUPS[2])
        return selected

    def _retrieve_evidence(self, query: str, route: list[dict], max_seconds: int = 45) -> list[EvidenceItem]:
        start = datetime.utcnow().timestamp()
        seen_urls: set[str] = set()
        seen_snippets: set[str] = set()
        evidence: list[EvidenceItem] = []
        
        all_results = []
        print(f"[Agent] Routing query to: {[g['id'] for g in route]}")
        for group in route:
            if group["id"] == "guidelines":
                nice_res = search_nice(query)
                print(f"[Agent] NICE search found {len(nice_res)} potential links")
                for r in nice_res: all_results.append((r, group))
                for domain in ["who.int", "cdc.gov"]:
                    if domain in group["domains"]:
                        res = search_generic_site(domain, query)
                        print(f"[Agent] {domain} search found {len(res)} potential links")
                        for r in res: all_results.append((r, group))
            elif group["id"] == "consumer":
                res = search_medline(query)
                print(f"[Agent] MedlinePlus search found {len(res)} potential links")
                for r in res: all_results.append((r, group))
            elif group["id"] == "research":
                res = search_pubmed(query)
                print(f"[Agent] PubMed search found {len(res)} potential links")
                for r in res: all_results.append((r, group))
            elif group["id"] == "drugs":
                for domain in ["fda.gov", "dailymed.nlm.nih.gov"]:
                    if domain in group["domains"]:
                        res = search_generic_site(domain, query)
                        print(f"[Agent] {domain} search found {len(res)} potential links")
                        for r in res: all_results.append((r, group))
        
        def result_priority(item: tuple[dict[str, Any], dict[dict, Any]]) -> int:
            res, group = item
            url = res["url"].lower()
            title = res.get("title", "").lower()
            priority = res.get("rank_boost", 0)
            if group["id"] == "guidelines":
                priority += 20
                if "nice.org.uk" in url: priority += 30
                if "who.int" in url or "cdc.gov" in url: priority += 15
            if any(kw in (url + title) for kw in ["pharmacological", "recommendation", "management", "treatment", "guideline"]):
                priority += 15
            is_pregnancy_query = "pregnancy" in query.lower() or "pregnant" in query.lower()
            if "pregnancy" in (url + title) and not is_pregnancy_query: priority -= 40
            is_child_query = "child" in query.lower() or "young" in query.lower() or "pediatric" in query.lower()
            if ("child" in (url + title) or "young people" in (url + title)) and not is_child_query: priority -= 30
            q_lower = query.lower()
            if "hypertension" in q_lower and ("ng136" in url or "hypertension" in title.lower()): priority += 40
            elif "diabetes" in q_lower and ("ng28" in url or "diabetes" in title.lower()): priority += 40
            elif "pneumonia" in q_lower and ("ng250" in url or "pneumonia" in title.lower()): priority += 40
            elif "heart failure" in q_lower and ("ng106" in url or "heart failure" in title.lower()): priority += 40
            elif "atrial fibrillation" in q_lower and ("ng196" in url or "atrial fibrillation" in title.lower()): priority += 40
            return priority

        all_results.sort(key=result_priority, reverse=True)
        print(f"[Agent] Total potential results after sorting: {len(all_results)}")

        for result, group in all_results:
            if datetime.utcnow().timestamp() - start > max_seconds: 
                print("[Agent] Retrieval timeout reached")
                break
            url = result["url"]
            if url in seen_urls: continue
            host = normalize_host(url)
            if not any(allowed in host for allowed in allowed_hosts()): continue
            
            print(f"[Agent] Fetching page: {url}")
            page = fetch_page(url)
            if not page or not page["text"]: 
                print(f"[Agent] Failed to fetch content for {url}")
                continue
            
            snippet = result.get("snippet") or summarize_for_evidence(page["text"], query)
            if not snippet or len(snippet) < 100: 
                print(f"[Agent] No relevant content snippet found in {url}")
                continue
            
            snippet_core = snippet[:500].strip().lower()
            if snippet_core in seen_snippets: continue
            
            seen_urls.add(url)
            seen_snippets.add(snippet_core)
            evidence.append(EvidenceItem(source_group=group["label"], title=result.get("title") or page["title"] or url, url=url, snippet=snippet))
            print(f"[Agent] Evidence item added from {host}. Total items: {len(evidence)}")
            if len(evidence) >= 6: break
        
        print(f"[Agent] Final evidence count: {len(evidence)}")
        return evidence

    def ask(self, query: str) -> dict[str, Any]:
        if self.client is None: return {"ok": False, "status": 503, "error": "NVIDIA_API_KEY is not configured."}
        route = self._route(query)
        evidence = self._retrieve_evidence(query, route)
        if not evidence:
            fallback = self._fallback_answer(query, evidence)
            fallback["route"] = [g["label"] for g in route]
            return fallback
        evidence_payload = [{"id": f"S{i+1}", "title": item.title, "url": item.url, "sourceGroup": item.source_group, "snippet": item.snippet} for i, item in enumerate(evidence)]
        draft = self._llm_json([{"role": "system", "content": ("You are a professional medical evidence assistant. Use ONLY the provided evidence snippets to answer the question. Prioritize identifying specific medications, first-line treatments, and clinical recommendations. Use [S1], [S2] for citations. Return JSON with 'answer' and 'confidence'.")}, {"role": "user", "content": f"Question: {query}\n\nEvidence:\n" + json.dumps(evidence_payload)}])
        return {"ok": True, "answer": draft.get("answer", "Insufficient evidence"), "evidence": evidence_payload, "needsHumanReview": True, "confidence": draft.get("confidence", 0.0), "route": [g["label"] for g in route]}

    def _fallback_answer(self, query: str, evidence: list) -> dict:
        return {"ok": True, "answer": "Insufficient evidence", "evidence": [], "needsHumanReview": True, "confidence": 0.0}

    def test_nvidia(self) -> dict[str, Any]:
        if not self.client: return {"ok": False, "message": "NVIDIA_API_KEY not set"}
        try:
            self.client.chat.completions.create(model=self.model, messages=[{"role": "user", "content": "hi"}], max_tokens=5)
            return {"ok": True, "message": "NVIDIA API key is working."}
        except Exception as e: return {"ok": False, "message": f"NVIDIA API test failed: {str(e)}"}
