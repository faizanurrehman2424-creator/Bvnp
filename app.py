import os
import json
import pandas as pd
from flask import Flask, request, jsonify, send_file, render_template
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from fpdf import FPDF
import google.generativeai as genai
from jobspy import scrape_jobs
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import requests

app = Flask(__name__)

# --- CONFIGURATION ---
UPLOAD_FOLDER = 'uploads'
STATIC_FOLDER = 'static'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True) 
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# 1. SETUP GEMINI API
GENAI_API_KEY = os.environ.get("GENAI_API_KEY") # <--- REPLACE WITH YOUR KEY
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
        # 1. Full Width Blue Header (100% Width)
        self.set_fill_color(30, 58, 138) # BVnP Blue
        self.rect(0, 0, 210, 35, 'F')    # Reduced height to 35
        
        # 2. Add Logo (Centered, Larger)
        logo_path = os.path.join(app.root_path, 'static', 'logo.png')
        if os.path.exists(logo_path):
            # BIGGER LOGO: w=90, CENTERED X=60
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
    try:
        model = genai.GenerativeModel('gemini-flash-latest')
        
        # --- UPDATED PROMPT: INSTRUCTS AI ON KEYWORDS ---
        prompt = f"""
        You are an expert CV Anonymizer for "Bart Vink & Partners".
        Input CV Text: {text}

        TASKS:
        1. Identify Real Name.
        2. ANONYMIZE content (Remove Name, Email, Phone).
        3. Structure data.
        4. Generate 3-5 Job Search Keywords. 
           CRITICAL: Do NOT use generic terms like "Consultant" or "Manager" alone. 
           Use specific skills like "Data Governance", "Product Owner", "Business Analyst".
           Do NOT use words like "Recruitment", "Staffing", or "Agency".

        OUTPUT JSON ONLY:
        {{
            "real_name": "Name",
            "search_keywords": ["Specific Skill 1", "Specific Skill 2"],
            "structured_cv": {{
                "role_title": "Role Title",
                "summary": "Summary text...",
                "skills": ["Skill1", "Skill2"],
                "languages": ["Lang1"],
                "experience": [
                    {{ "title": "Job Title", "company": "Company", "dates": "2020-2022", "description": "Details..." }}
                ],
                "education": [
                    {{ "degree": "Degree", "school": "School", "dates": "2019" }}
                ]
            }}
        }}
        """
        response = model.generate_content(prompt)
        clean_json = response.text.replace("```json", "").replace("```", "")
        return json.loads(clean_json)
    except Exception as e:
        print(f"API Error: {e}")
        return {"real_name": "Error", "structured_cv": {}}

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
    keywords = data.get('keywords', [])
    candidate_name = data.get('real_name', 'Unknown')
    
    if not keywords: return jsonify({"error": "No keywords"}), 400

    # 1. SETUP JOOBLE
    # Ideally, get this from environment variables: os.environ.get("JOOBLE_KEY")
    # For now, you can paste it here to test, but move to Env Vars for security later.
    API_KEY = os.environ.get("JOOBLE_KEY") 
    BASE_URL = "https://jooble.org/api/"
    
    # 2. PREPARE REQUEST
    # Jooble likes a single string of keywords
    keyword_string = " ".join(keywords)
    
    payload = {
        "keywords": keyword_string,
        "location": "Netherlands"
    }
    
    print(f"🔎 Searching Jooble for: {keyword_string}")

    try:
        response = requests.post(BASE_URL + API_KEY, json=payload)
        jooble_data = response.json()
        raw_jobs = jooble_data.get('jobs', [])

        # --- NEW: STRICT FILTERING LOGIC ---
        forbidden_titles = ["recruiter", "talent acquisition", "hr manager", "floor manager", "financial analyst"]
        forbidden_companies = ["agency", "werving", "selectie"]

        formatted_jobs = []
        for job in raw_jobs:
            title = job.get('title', '').lower()
            company = job.get('company', '').lower()
            
            # 1. Check if a forbidden word is in the TITLE
            if any(bad_word in title for bad_word in forbidden_titles):
                continue # Skip this job
                
            # 2. Check if a forbidden word is in the COMPANY
            if any(bad_word in company for bad_word in forbidden_companies):
                continue # Skip this job

            # 3. If it passes, add it!
            formatted_jobs.append({
                "title": job.get('title', 'No Title'),
                "company": job.get('company', 'Unknown'),
                "location": job.get('location', 'Netherlands'),
                "job_url": job.get('link'),
                "description": job.get('snippet', '') 
            })
        if sheet and formatted_jobs:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for job in formatted_jobs:
                row = [timestamp, candidate_name, job['title'], job['company'], job['location'], job['job_url']]
                try: sheet.append_row(row)
                except: pass
    

        # ... (save to sheets code) ...
        
        return jsonify(formatted_jobs)

    except Exception as e:
        print(f"API Error: {e}")
        return jsonify({"error": str(e)}), 500
        
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
    # Render assigns a random port, we must listen to it
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
