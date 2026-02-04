import os
import json
import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from fpdf import FPDF
import google.generativeai as genai
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import requests
import re

app = Flask(__name__)

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 1. SETUP GEMINI API
GENAI_API_KEY = os.environ.get("GENAI_API_KEY")
if GENAI_API_KEY:
    genai.configure(api_key=GENAI_API_KEY)

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
        # self.rect(0, 0, 210, 35, 'F') 
        
        logo_path = os.path.join(app.root_path, 'static', 'logo.png')
        if os.path.exists(logo_path):
            self.image(logo_path, x=60, y=2, w=90)
        self.ln(45)

    def footer(self):
        self.set_y(-40)
        col1, col2, col3, col4, col5 = 10, 50, 90, 130, 165
        y_head = self.get_y()
        y_content = y_head + 4
        
        self.set_font('Arial', 'B', 7)
        self.set_text_color(30, 58, 138)
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
        left_col_x = 15
        right_col_x = 65
        right_col_width = 130
        y_start = self.get_y()
        estimated_lines = len(content) / 90
        estimated_height = max(10, estimated_lines * 5)
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
        model = genai.GenerativeModel('gemini-flash-latest')
        
        prompt = f"""
        You are an expert Headhunter.
        Input CV Text: {text}

        TASKS:
        1. Identify Real Name.
        2. ANONYMIZE content (Remove Name, Email, Phone, Address, LinkedIn).
        3. Identify Seniority.
        4. Generate Search Data (Job Titles & Avoid List).

        CRITICAL: You MUST populate the "structured_cv" first. Do not summarize; keep the details.

        RETURN JSON ONLY:
        {{
            "structured_cv": {{
                "role_title": "The Anonymized Role Title",
                "summary": "Anonymized professional summary...",
                "skills": ["Skill1", "Skill2"],
                "languages": ["Lang1"],
                "experience": [
                    {{ "title": "Job Title", "company": "Company/Industry", "dates": "Dates", "description": "Full description..." }}
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
        
        response = model.generate_content(
            prompt,
            generation_config={
                "response_mime_type": "application/json",
                "max_output_tokens": 8192 
            }
        )
        
        # --- NEW: ROBUST JSON PARSER ---
        # Sometimes AI cuts off. We try to fix it or fallback safely.
        try:
            data = json.loads(response.text)
        except json.JSONDecodeError:
            print("⚠️ JSON Cutoff Detected! Attempting simple repair...")
            # If JSON is cut off, we just assume the important parts (structured_cv) are there
            # because we put them FIRST in the schema.
            # We will try to close the brackets manually to salvage data.
            safe_text = response.text.strip()
            if not safe_text.endswith("}"):
                safe_text += '}' # Try closing main object
            if not safe_text.endswith("}"):
                safe_text += '}'
            
            try:
                data = json.loads(safe_text)
            except:
                print("❌ Repair failed. Returning partial/error data.")
                return {
                    "real_name": "Unknown",
                    "job_titles": ["Developer"],
                    "keywords_to_avoid": [],
                    "structured_cv": {"summary": "Error: CV too long for AI processing."}
                }
        
        # Fallback if experience is empty
        if not data.get("structured_cv", {}).get("experience"):
            print("⚠️ AI returned empty experience! Using fallback.")
            data["structured_cv"]["experience"] = [{
                "title": "Experience Section",
                "company": "See Summary", 
                "dates": "Present",
                "description": "Please refer to the original CV for full details."
            }]
            
        return data

    except Exception as e:
        print(f"!!! AI ERROR: {e} !!!")
        return {
            "real_name": "Error", 
            "job_titles": ["Business Analyst"], 
            "keywords_to_avoid": [],
            "structured_cv": {
                "role_title": "Error Processing CV",
                "summary": "The AI could not process this file.",
                "skills": [],
                "languages": [],
                "experience": [],
                "education": []
            }
        }

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

    skills_text = ", ".join(cv_data.get('skills', []))
    langs_text = ", ".join(cv_data.get('languages', []))
    full_skills = f"Skills: {skills_text}\n\nTalen: {langs_text}"
    
    pdf.section_row("VAARDIGHEDEN\n& TALEN", full_skills)

    if cv_data.get('experience'):
        first = True
        for job in cv_data['experience']:
            heading = "WERKERVARING" if first else ""
            title = job.get('title', 'N/A')
            comp = job.get('company', 'N/A')
            dates = job.get('dates', 'N/A')
            desc = job.get('description', '')
            job_block = f"{title}\n{comp} | {dates}\n\n{desc}"
            pdf.section_row(heading, job_block)
            first = False

    if cv_data.get('education'):
        first = True
        for edu in cv_data['education']:
            heading = "OPLEIDING" if first else ""
            degree = edu.get('degree', 'N/A')
            school = edu.get('school', 'N/A')
            dates = edu.get('dates', 'N/A')
            edu_block = f"{degree}\n{school} | {dates}"
            pdf.section_row(heading, edu_block)
            first = False

    output_path = os.path.join(app.config['UPLOAD_FOLDER'], 'anonymous_cv.pdf')
    pdf.output(output_path)
    return send_file(output_path, as_attachment=True)

@app.route('/search_jobs', methods=['POST'])
def search_jobs():
    data = request.json
    raw_titles = data.get('job_titles', [])
    candidate_name = data.get('real_name', 'Unknown')
    
    # 1. GENERATE SMART QUERIES ("Onion Strategy")
    search_queries = []
    for t in raw_titles[:2]: 
        clean_t = re.sub(r'\(.*?\)', '', t).replace('/', ' ').strip()
        if clean_t not in search_queries: search_queries.append(clean_t)
        
        # Add broader version (first 2 words)
        words = clean_t.split()
        if len(words) >= 2:
            short_t = " ".join(words[:2])
            if short_t not in search_queries: search_queries.append(short_t)
    
    # Fallback
    search_queries.append("Business Analyst") 

    # 2. SETUP JOOBLE
    JOOBLE_KEY = os.environ.get("JOOBLE_KEY")
    API_URL = "https://jooble.org/api/" + JOOBLE_KEY
    
    raw_results = []
    seen_urls = set()

    # 3. FETCH LOOP
    print(f"🔎 Search Plan: {search_queries}")
    
    for title in search_queries:
        if len(raw_results) >= 25: break # Stop if we have enough
            
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

    # 4. STRICT FILTERING (Updated Blocklist)
    # This blocklist removes the specific "junk" roles you saw
    forbidden_words = [
        # Agencies
        "recruitment", "agency", "staffing", "werving", "selectie", "headhunter", "talent acquisition",
        # Non-Tech / Irrelevant Roles
        "cleaner", "housekeeping", "floor manager", "store manager", "sales director", "sales manager",
        "marketing", "content", "social media", "financial analyst", "accountant", "audit", "tax",
        "warehouse", "logistics", "driver", "operator", "commissioning", "internship", "stage"
    ]
    
    final_jobs = []
    
    for job in raw_results:
        title = job.get('title', '').lower()
        company = job.get('company', '').lower()
        link = job.get('link')

        # FILTER: Block if any forbidden word is in the TITLE or COMPANY
        if any(bad in title for bad in forbidden_words): continue
        if any(bad in company for bad in forbidden_words): continue
        
        final_jobs.append({
            "title": job.get('title'),
            "company": job.get('company', 'Unknown'),
            "location": job.get('location', 'Netherlands'),
            "job_url": link,
            "description": job.get('snippet', '') 
        })

    # Limit to top 20
    final_jobs = final_jobs[:20]

    # 5. SAVE TO SHEETS
    if sheet and final_jobs:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for job in final_jobs:
            row = [timestamp, candidate_name, job['title'], job['company'], job['location'], job['job_url']]
            try: sheet.append_row(row)
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
