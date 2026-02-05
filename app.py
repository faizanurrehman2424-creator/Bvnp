import os
import json
import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from fpdf import FPDF
from openai import OpenAI  # <--- CHANGED: Switched to OpenAI
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import requests
import re
import time 

app = Flask(__name__)

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 1. SETUP OPENAI API (CHANGED)
# Make sure to add "OPENAI_API_KEY" to your Render Environment Variables
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
client = None
if OPENAI_API_KEY:
    client = OpenAI(api_key=OPENAI_API_KEY)

# 2. SETUP GOOGLE SHEETS
try:
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    client_gs = gspread.authorize(creds)
    sheet = client_gs.open("Candidate_Jobs_DB").sheet1
except Exception as e:
    print(f"Warning: Google Sheets not connected. Error: {e}")
    sheet = None

# --- PDF GENERATOR CLASS ---
class BVnP_PDF(FPDF):
    def header(self):
        self.set_fill_color(30, 58, 138)
        logo_path = os.path.join(app.root_path, 'static', 'logo.png')
        if os.path.exists(logo_path):
            self.image(logo_path, x=60, y=2, w=90)
        self.ln(45)

    def footer(self):
        self.set_y(-40)
        self.set_font('Arial', 'B', 7)
        self.set_text_color(30, 58, 138)
        col1, col2, col3, col4, col5 = 10, 50, 90, 130, 165
        y_head = self.get_y()
        y_content = y_head + 4
        
        self.text(col1, y_head, "POST")
        self.text(col2, y_head, "BEZOEK")
        self.text(col3, y_head, "CONTACT")
        self.text(col4, y_head, "KVK UTRECHT")
        self.text(col5, y_head, "BANK")

        self.set_font('Arial', '', 6)
        self.set_text_color(80, 80, 80)
        self.set_xy(col1, y_content)
        self.multi_cell(35, 3, "Atoomweg 63\n3542AA Utrecht\nNederland")
        self.set_xy(col2, y_content)
        self.multi_cell(35, 3, "Atoomweg 63\n3542AA Utrecht\nwww.bvenp.nl")
        self.set_xy(col3, y_content)
        self.multi_cell(35, 3, "T 030 3200250\nE info@bvenp.nl")
        self.set_xy(col4, y_content)
        self.multi_cell(30, 3, "61798053\n\nBTW\nNL854492793801")
        self.set_xy(col5, y_content)
        self.multi_cell(35, 3, "NL57INGB0008955004\n\nBIC\nINGBNL2A")
        self.set_xy(10, -10)
        self.set_font('Arial', 'I', 5)
        self.set_text_color(150, 150, 150)
        self.cell(0, 5, "Op onze overeenkomsten zijn de algemene voorwaarden van Bart Vink & Partners van toepassing.", 0, 0, 'C')

    def section_row(self, heading, content):
        left_col_x, right_col_x, right_col_width = 15, 65, 130
        y_start = self.get_y()
        estimated_height = max(10, (len(content) / 90) * 5)
        if y_start + estimated_height > 250:
            self.add_page()
            y_start = self.get_y()
        self.set_xy(left_col_x, y_start)
        self.set_font("Arial", "B", 10)
        self.set_text_color(30, 58, 138)
        self.multi_cell(45, 6, heading.upper())
        self.set_xy(right_col_x, y_start)
        self.set_font("Arial", "", 10)
        self.set_text_color(0, 0, 0)
        safe_content = content.encode('latin-1', 'replace').decode('latin-1')
        self.multi_cell(right_col_width, 6, safe_content)
        self.ln(6)

# --- HELPER FUNCTIONS ---

def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text

def ai_process_cv(text):
    print(f"--- 1. PDF TEXT LENGTH: {len(text)} ---")
    try:
        # CHANGED: OpenAI Call
        response = client.chat.completions.create(
            model="gpt-4o",  # Using the Pro/Flagship model
            messages=[
                {
                    "role": "system", 
                    "content": "You are an expert Headhunter. You must response in JSON format."
                },
                {
                    "role": "user", 
                    "content": f"""
                    Input CV Text: {text}

                    TASKS:
                    1. Identify Real Name.
                    2. ANONYMIZE content.
                    3. Identify Seniority.
                    4. Generate Search Data (Job Titles & Avoid List).

                    CRITICAL: Populate "structured_cv" first.

                    RETURN JSON ONLY with this structure:
                    {{
                        "structured_cv": {{
                            "role_title": "Anonymized Role",
                            "summary": "Summary...",
                            "skills": ["Skill1", "Skill2"],
                            "languages": ["Lang1"],
                            "experience": [
                                {{ "title": "Job Title", "company": "Company", "dates": "Dates", "description": "Details..." }}
                            ],
                            "education": [
                                {{ "degree": "Degree", "school": "School", "dates": "Dates" }}
                            ]
                        }},
                        "real_name": "Name",
                        "job_titles": ["Title1", "Title2"],
                        "keywords_to_avoid": ["Avoid1", "Avoid2"]
                    }}
                    """
                }
            ],
            response_format={ "type": "json_object" }
        )
        
        response_content = response.choices[0].message.content
        
        try:
            data = json.loads(response_content)
        except json.JSONDecodeError:
            print("⚠️ JSON Cutoff Detected! Attempting repair...")
            safe_text = response_content.strip()
            if not safe_text.endswith("}"): safe_text += '}'
            try: data = json.loads(safe_text)
            except: return {"real_name": "Unknown", "job_titles": ["Developer"], "keywords_to_avoid": [], "structured_cv": {"summary": "Error parsing."}}
            
        if not data.get("structured_cv", {}).get("experience"):
            data["structured_cv"]["experience"] = [{"title": "Experience", "company": "See Summary", "dates": "Present", "description": "Refer to original CV."}]
            
        return data

    except Exception as e:
        print(f"!!! AI ERROR: {e} !!!")
        return {"real_name": "Error", "job_titles": ["Business Analyst"], "keywords_to_avoid": [], "structured_cv": {"summary": "Error processing file.", "skills": [], "experience": [], "education": []}}

def get_ai_scores(jobs, skills_list):
    """
    Batch processes job scoring with Auto-Retry for Rate Limits.
    """
    if not jobs or not skills_list: return jobs

    # Only score top 10 to keep it fast
    jobs_to_score = jobs[:10]
    
    # Create prompt with Titles and Snippets
    job_text_block = ""
    for idx, job in enumerate(jobs_to_score):
        job_text_block += f"ID {idx}: {job['title']} at {job['company']} - {job['description']}\n"

    # CHANGED: OpenAI Message Structure
    messages = [
        {
            "role": "system",
            "content": "You are a Recruiter. Return JSON only."
        },
        {
            "role": "user",
            "content": f"""
            Candidate Skills: {", ".join(skills_list)}
            Jobs to Evaluate:
            {job_text_block}
            Task: Rate each job (0-100) based on relevance to candidate skills.
            Provide a SHORT 1-sentence reason.
            RETURN JSON ONLY:
            {{
                "scores": [
                    {{ "id": 0, "score": 95, "reason": "Perfect match for Cloud skills." }}
                ]
            }}
            """
        }
    ]

    try:
        # ATTEMPT 1
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=messages,
            response_format={ "type": "json_object" }
        )
    except Exception as e:
        if "429" in str(e):
            print("⚠️ OpenAI Quota Exceeded (429). Waiting 60 seconds to cooldown...")
            time.sleep(65) # Wait 65s to be safe
            print("♻️ Retrying AI Scoring...")
            try:
                # ATTEMPT 2 (Retry)
                response = client.chat.completions.create(
                    model="gpt-4o",
                    messages=messages,
                    response_format={ "type": "json_object" }
                )
            except Exception as retry_e:
                print(f"❌ Retry failed: {retry_e}")
                return jobs # Return jobs without scores if retry fails
        else:
            print(f"Scoring Error: {e}")
            return jobs

    # Parse Response
    try:
        response_content = response.choices[0].message.content
        score_data = json.loads(response_content)
        for item in score_data.get("scores", []):
            idx = item.get("id")
            if idx is not None and idx < len(jobs_to_score):
                jobs[idx]["match_score"] = item.get("score")
                jobs[idx]["match_reason"] = item.get("reason")
    except Exception as parse_e:
        print(f"JSON Parse Error: {parse_e}")

    return jobs

# --- ROUTES ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/anonymize', methods=['POST'])
def anonymize():
    if 'file' not in request.files: return jsonify({"error": "No file"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"error": "No file"}), 400
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(file.filename))
    file.save(filepath)
    try:
        raw_text = extract_text_from_pdf(filepath)
        ai_result = ai_process_cv(raw_text)
        return jsonify(ai_result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/download_pdf', methods=['POST'])
def download_pdf():
    data = request.json
    cv_data = data.get('structured_cv', {})
    pdf = BVnP_PDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=45)
    pdf.set_font("Arial", "B", 16)
    pdf.set_text_color(30, 58, 138)
    pdf.cell(0, 10, cv_data.get('role_title', 'CANDIDATE PROFILE'), 0, 1, 'C')
    pdf.set_font("Arial", "I", 10)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 8, "Anoniem Curriculum Vitae", 0, 1, 'C')
    pdf.ln(10)
    
    skills = ", ".join(cv_data.get('skills', []))
    langs = ", ".join(cv_data.get('languages', []))
    pdf.section_row("VAARDIGHEDEN", f"Skills: {skills}\n\nTalen: {langs}")

    if cv_data.get('experience'):
        first = True
        for job in cv_data['experience']:
            heading = "WERKERVARING" if first else ""
            pdf.section_row(heading, f"{job.get('title')}\n{job.get('company')} | {job.get('dates')}\n\n{job.get('description')}")
            first = False

    if cv_data.get('education'):
        first = True
        for edu in cv_data['education']:
            heading = "OPLEIDING" if first else ""
            pdf.section_row(heading, f"{edu.get('degree')}\n{edu.get('school')} | {edu.get('dates')}")
            first = False

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'anonymous_cv.pdf')
    pdf.output(output_path)
    return send_file(output_path, as_attachment=True)

@app.route('/search_jobs', methods=['POST'])
def search_jobs():
    data = request.json
    raw_titles = data.get('job_titles', [])
    candidate_name = data.get('real_name', 'Unknown')
    candidate_skills = data.get('cv_skills', []) # Receive Skills
    
    # 1. SMART QUERIES (Onion Strategy)
    search_queries = []
    for t in raw_titles[:2]: 
        clean_t = re.sub(r'\(.*?\)', '', t).replace('/', ' ').strip()
        if clean_t not in search_queries: search_queries.append(clean_t)
        
        words = clean_t.split()
        if len(words) >= 2:
            short_t = " ".join(words[:2])
            if short_t not in search_queries: search_queries.append(short_t)
    
    search_queries.append("Business Analyst") 

    # 2. JOOBLE FETCH
    JOOBLE_KEY = os.environ.get("JOOBLE_KEY")
    API_URL = "https://jooble.org/api/" + JOOBLE_KEY
    
    raw_results = []
    seen_urls = set()

    print(f"🔎 Search Plan: {search_queries}")
    
    for title in search_queries:
        if len(raw_results) >= 25: break 
        
        print(f"🔎 Jooble Searching: {title}")
        payload = { "keywords": title, "location": "Netherlands", "page": 1 }

        try:
            response = requests.post(API_URL, json=payload)
            data = response.json()
            jobs = data.get('jobs', [])
            
            if jobs:
                print(f"✅ Found {len(jobs)} jobs for '{title}'")
                for j in jobs:
                    if j.get('link') not in seen_urls:
                        raw_results.append(j)
                        seen_urls.add(j.get('link'))
        except Exception as e:
            print(f"Jooble Error: {e}")
            continue

    # 3. ULTRA-STRICT FILTERING
    forbidden_words = [
        "recruitment", "recruiter", "talent acquisition", "hr manager", "human resources", 
        "headhunter", "agency", "staffing", "werving", "selectie",
        "supply chain", "logistics", "warehouse", "operations manager", "floor manager", 
        "store manager", "category manager", "facility", "coordinator", "commissioning",
        "quality", "environmental", "audit", "compliance", "legal",
        "sales", "account manager", "marketing", "commercial", "growth", "sme",
        "financial", "accountant", "tax", "treasury", "controller",
        "cleaner", "driver", "mechanic", "nurse", "teacher", "internship", "stage"
    ]
    
    final_jobs = []
    for job in raw_results:
        title = job.get('title', '').lower()
        company = job.get('company', '').lower()
        link = job.get('link')

        if any(bad in title for bad in forbidden_words): continue
        if any(bad in company for bad in forbidden_words): continue
        
        final_jobs.append({
            "title": job.get('title'),
            "company": job.get('company', 'Unknown'),
            "location": job.get('location', 'Netherlands'),
            "job_url": link,
            "description": job.get('snippet', ''),
            "match_score": 0,
            "match_reason": "Analyzing..."
        })

    final_jobs = final_jobs[:15]

    # 4. AI SCORING
    if candidate_skills:
        print("🤖 calculating AI Match Scores...")
        final_jobs = get_ai_scores(final_jobs, candidate_skills)

    # 5. SAVE
    if sheet and final_jobs:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for job in final_jobs:
            try: sheet.append_row([timestamp, candidate_name, job['title'], job['company'], job['location'], job['job_url']])
            except: pass
    
    return jsonify(final_jobs)

@app.route('/generate_csv', methods=['POST'])
def generate_csv():
    data = request.json
    jobs = data.get('jobs', [])
    if not jobs: return jsonify({"error": "No jobs"}), 400
    df = pd.DataFrame(jobs)
    csv_path = os.path.join(app.config['UPLOAD_FOLDER'], 'found_jobs.csv')
    df.to_csv(csv_path, index=False)
    return send_file(csv_path, as_attachment=True)

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
