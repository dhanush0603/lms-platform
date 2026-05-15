import shutil
import cloudconvert
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, send_from_directory
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask_mail import Mail, Message 
import secrets
from datetime import datetime, timedelta
import re 
import os
import fitz 
import spacy
from collections import Counter
from string import punctuation
import json
import google.generativeai as genai
import random

# --- APP CONFIGURATION ---
app = Flask(__name__)
app.secret_key = 'your_super_secret_key' 
GEMINI_API_KEY = "API KEY" 
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash-latest')

app.config['UPLOAD_FOLDER'] = 'uploads'

nlp = spacy.load('en_core_web_sm')

# --- MAIL CONFIGURATION ---
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = 'dhanushvenu999@gmail.com'
app.config['MAIL_PASSWORD'] = 'bcjj ggbg esfc mfma'
mail = Mail(app)

# --- DATABASE HELPER FUNCTION ---
def get_db_connection():
    conn = sqlite3.connect('lms.db', timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def summarize_text(text, num_sentences=5):
    """
    A simple extractive summarizer using spaCy.
    """
    # Process the entire text with spaCy
    doc = nlp(text)

    # Get a list of common "stop words" (like 'the', 'a', 'is')
    stop_words = spacy.lang.en.stop_words.STOP_WORDS

    # Calculate the frequency of each word that isn't a stop word or punctuation
    word_frequencies = Counter(
        [token.text.lower() for token in doc if token.text.lower() not in stop_words and token.text not in punctuation]
    )

    # Normalize the frequencies
    max_frequency = max(word_frequencies.values())
    for word in word_frequencies.keys():
        word_frequencies[word] = (word_frequencies[word] / max_frequency)

    # Score each sentence by summing the frequencies of its words
    sentence_scores = {}
    for sent in doc.sents:
        for word in sent:
            if word.text.lower() in word_frequencies.keys():
                if sent not in sentence_scores.keys():
                    sentence_scores[sent] = word_frequencies[word.text.lower()]
                else:
                    sentence_scores[sent] += word_frequencies[word.text.lower()]

    # Get the top N sentences with the highest scores
    summarized_sentences = sorted(sentence_scores, key=sentence_scores.get, reverse=True)[:num_sentences]
    
    # Convert the spaCy Span objects back to strings
    final_summary = [sent.text for sent in summarized_sentences]
    
    return final_summary

# --- ROUTING ---
@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        conn = get_db_connection()
        user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
        conn.close()

        if not user or user['status'] == 'pending': # FIX: Check if user exists or is pending
            return render_template('login.html', email_error='No active account is registered with this email.', email=email)
        
        # FIX: Check that user['password'] is not None before checking hash
        if not user['password'] or not check_password_hash(user['password'], password):
            return render_template('login.html', 
                                   password_error='Incorrect password. Please try again.', 
                                   email=email)

        session['user_id'] = user['id']
        session['user_role'] = user['role']
        session['user_name'] = user['name']

        if user['role'] == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            return redirect(url_for('intern_dashboard'))

    return render_template('login.html')

@app.route('/check_email', methods=['POST'])
def check_email():
    email = request.json.get('email')
    conn = get_db_connection()
    user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    return jsonify({'exists': user is not None})

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        password = request.form['password']
        
        # Check password complexity before hashing
        password_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{15,}$"
        if not re.match(password_regex, password):
            return render_template('signup.html', email_error='Password does not meet complexity requirements.', name=name, email=email)

        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        conn = get_db_connection()
        try:
            # Check if email exists first
            existing_user = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
            if existing_user:
                 return render_template('signup.html', email_error='This email is already registered.', name=name, email=email)

            # Insert the new user
            cursor = conn.cursor()
            cursor.execute('INSERT INTO users (name, email, password, role, status) VALUES (?, ?, ?, ?, ?)',
                           (name, email, hashed_password, 'intern', 'active'))
            new_user_id = cursor.lastrowid
            conn.commit()

            # --- CORRECTED EMAIL LOGIC ---
            # Send a welcome email to the new intern
            try:
                msg = Message('Welcome to the LMS!', 
                              sender=app.config['MAIL_USERNAME'], 
                              recipients=[email])
                msg.body = f"Hi {name},\n\nWelcome to the Learning Management System! You can now log in with your email and password."
                mail.send(msg)
            except Exception as e:
                # Log the email error but don't stop the user from signing up
                print(f"EMAIL-ERROR on signup: {e}") 
            # -----------------------------

            # Set session variables for the new user
            session['user_id'] = new_user_id
            session['user_role'] = 'intern'
            session['user_name'] = name
            
            return redirect(url_for('intern_dashboard'))

        except sqlite3.Error as e:
            conn.rollback()
            print(f"DATABASE-ERROR on signup: {e}")
            return "A database error occurred.", 500
        finally:
            conn.close()
    
    return render_template('signup.html')

@app.route('/forgot_password')
def forgot_password():
    return render_template('forgot-password.html')

# --- DASHBOARD ROUTES ---
@app.route('/admin_dashboard')
def admin_dashboard():
    if 'user_role' not in session or session['user_role'] != 'admin':
        return redirect(url_for('login'))
    
    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
    admins = conn.execute("SELECT * FROM users WHERE role = 'admin' AND status = 'active'").fetchall()
    interns = conn.execute("SELECT * FROM users WHERE role = 'intern' AND status = 'active'").fetchall()
    
    # --- UPDATED: Fetch two lists of courses ---
    all_courses_rows = conn.execute("SELECT * FROM courses ORDER BY id DESC").fetchall()
    all_courses = [dict(row) for row in all_courses_rows]
    
    recent_courses_rows = conn.execute("SELECT * FROM courses ORDER BY id DESC LIMIT 3").fetchall()
    recent_courses = [dict(row) for row in recent_courses_rows]
    conn.close()

    # Pass BOTH lists to the template
    return render_template('admin-dashboard.html', user=user, interns=interns, admins=admins, all_courses=all_courses, recent_courses=recent_courses)

@app.route('/intern_dashboard')
def intern_dashboard():
    if 'user_role' not in session or session['user_role'] != 'intern':
        return redirect(url_for('login'))

    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE id = ?", (session['user_id'],)).fetchone()
    
    # Get all courses, sorted from oldest to newest
    all_courses_rows = conn.execute("SELECT * FROM courses ORDER BY id ASC").fetchall()
    
    # Get all of the intern's passed quiz attempts
    passed_quizzes = conn.execute(
        "SELECT course_id FROM quiz_attempts WHERE user_id = ? AND status = 'Pass'",
        (session['user_id'],)
    ).fetchall()
    passed_course_ids = {q['course_id'] for q in passed_quizzes}

    # Determine the lock status for each course
    courses = []
    for i, course_row in enumerate(all_courses_rows):
        course = dict(course_row) # Convert row object to a mutable dictionary
        is_locked = True
        if i == 0:
            # The first course is always unlocked
            is_locked = False
        else:
            # A course is unlocked if the PREVIOUS course has been passed
            previous_course_id = all_courses_rows[i-1]['id']
            if previous_course_id in passed_course_ids:
                is_locked = False
        
        course['is_locked'] = is_locked
        courses.append(course)
    
    quiz_history = conn.execute("""
        SELECT c.name, qa.score, qa.total_questions
        FROM quiz_attempts qa
        JOIN courses c ON qa.course_id = c.id
        WHERE qa.user_id = ? AND qa.score IS NOT NULL
        ORDER BY qa.attempted_at DESC
    """, (session['user_id'],)).fetchall()
    
    conn.close()

    active_section = request.args.get('active_section', 'viewMaterials')
    
    return render_template('intern-dashboard.html', 
                           user=user, 
                           courses=courses, 
                           quiz_history=quiz_history,
                           active_section=active_section,
                           passed_course_ids=passed_course_ids) # Pass this for the 'Completed' badge

# --- ADMIN FUNCTIONALITY ---
@app.route('/add_admin', methods=['POST'])
def add_admin():
    if 'user_role' not in session or session['user_role'] != 'admin':
        return jsonify({'success': False, 'error': 'Permission denied.'}), 403

    name = request.form['name']
    email = request.form['email']
    conn = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (name, email, role) VALUES (?, ?, ?)', (name, email, 'admin'))
        new_user_id = cursor.lastrowid
        
        token = secrets.token_urlsafe(20)
        expires_at = datetime.now() + timedelta(hours=24)
        cursor.execute('INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)',
                     (new_user_id, token, expires_at))
        conn.commit()
        
        reset_link = url_for('set_password', token=token, _external=True)
        msg = Message('Set Your Password for LMS', sender=app.config['MAIL_USERNAME'], recipients=[email])
        msg.html = render_template('email/invitation_email.html', name=name, reset_link=reset_link)
        mail.send(msg)

        return jsonify({'success': True, 'message': f'Invitation email has been sent to {name}'})

    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': f"The email '{email}' is already registered."}), 400
    except Exception as e:
        print(f"ERROR in add_admin: {e}")
        return jsonify({'success': False, 'error': f"A server error occurred: {e}"}), 500
    finally:
        if conn:
            conn.close()

@app.route('/add_intern', methods=['POST'])
def add_intern():
    if 'user_role' not in session or session['user_role'] != 'admin':
        return jsonify({'success': False, 'error': 'Permission denied.'}), 403

    name = request.form['name']
    email = request.form['email']
    conn = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('INSERT INTO users (name, email, role) VALUES (?, ?, ?)', (name, email, 'intern'))
        new_user_id = cursor.lastrowid
        
        token = secrets.token_urlsafe(20)
        expires_at = datetime.now() + timedelta(hours=24)
        
        cursor.execute('INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)', (new_user_id, token, expires_at))
        conn.commit()
        
        reset_link = url_for('set_password', token=token, _external=True)
        msg = Message('Set Your Password for LMS', sender=app.config['MAIL_USERNAME'], recipients=[email])
        msg.html = render_template('email/invitation_email.html', user_name=name, reset_link=reset_link, current_year=datetime.now().year)
        mail.send(msg)
        
        return jsonify({'success': True, 'message': f'Invitation email has been sent to {name}.'})

    except sqlite3.IntegrityError:
        return jsonify({'success': False, 'error': f"The email '{email}' is already registered."}), 400
    except Exception as e:
        print(f"ERROR in add_intern: {e}")
        return jsonify({'success': False, 'error': 'A server error occurred.'}), 500
    finally:
        if conn:
            conn.close()

@app.route('/remove_user/<int:user_id>', methods=['POST'])
def remove_user(user_id):
    if 'user_role' not in session or session['user_role'] != 'admin':
        flash('You do not have permission to perform this action.', 'error')
        return redirect(url_for('login'))

    if user_id == session['user_id']:
        flash('You cannot remove your own account.', 'error')
        return redirect(url_for('admin_dashboard', active_section='admins'))

    conn = get_db_connection()
    conn.execute('DELETE FROM password_reset_tokens WHERE user_id = ?', (user_id,))
    conn.execute('DELETE FROM users WHERE id = ?', (user_id,))
    conn.commit()
    conn.close()

    flash('User has been removed successfully.', 'success')
    active_tab = request.form.get('active_tab', 'admins')
    return redirect(url_for('admin_dashboard', active_section=active_tab))

@app.route('/edit_profile', methods=['POST'])
def edit_profile():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    try:
        user_id = session['user_id']
        new_name = request.form.get('name')
        new_email = request.form.get('email')
        new_password = request.form.get('password')

        # --- NEW: Check if the new email is already taken by another user ---
        email_owner = conn.execute('SELECT id FROM users WHERE email = ? AND id != ?',
                                   (new_email, user_id)).fetchone()

        if email_owner:
            flash('That email address is already registered to another account.', 'error')
            return redirect(url_for('admin_dashboard', active_section='editProfile'))
        # --------------------------------------------------------------------

        if new_password:
            # Enforce password complexity
            password_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{15,}$"
            if not re.match(password_regex, new_password):
                flash('Password does not meet the complexity requirements.', 'error')
                return redirect(url_for('admin_dashboard', active_section='editProfile'))

            # Check if new password is the same as the old one
            user = conn.execute('SELECT password FROM users WHERE id = ?', (user_id,)).fetchone()
            if user and user['password'] and check_password_hash(user['password'], new_password):
                flash('New password cannot be the same as your current password.', 'error')
                return redirect(url_for('admin_dashboard', active_section='editProfile'))

        # Update name and email
        conn.execute('UPDATE users SET name = ?, email = ? WHERE id = ?',
                   (new_name, new_email, user_id))

        # Conditionally update password if a new one passed all checks
        if new_password:
            hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
            conn.execute('UPDATE users SET password = ? WHERE id = ?',
                         (hashed_password, user_id))

        conn.commit()

        session['user_name'] = new_name

        # Send confirmation email
        try:
           subject = "Your LMS Profile Has Been Updated"
           msg = Message(subject, sender=app.config['MAIL_USERNAME'], recipients=[new_email])
           msg.html = render_template('email/update_email.html', user_name=new_name, current_year=datetime.now().year)
           mail.send(msg)
        except Exception as e:
            print(f"EMAIL-ERROR on admin profile update: {e}")
            flash('Profile updated, but the confirmation email could not be sent.', 'error')
        else:
            flash('Your profile has been updated successfully!', 'success')

    finally:
        if conn:
            conn.close()

    return redirect(url_for('admin_dashboard', active_section='editProfile'))


# In app.py

# In app.py
# Make sure you have 'import re' at the top of your file

@app.route('/intern/edit_profile', methods=['POST'])
def intern_edit_profile():
    if 'user_role' not in session or session['user_role'] != 'intern':
        return redirect(url_for('login'))

    user_id = session['user_id']
    new_name = request.form.get('name')
    new_email = request.form.get('email')
    new_password = request.form.get('password')

    conn = get_db_connection()

    if new_password:
        # --- NEW: Enforce password complexity ---
        password_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{15,}$"
        if not re.match(password_regex, new_password):
            flash('Password does not meet the complexity requirements.', 'error')
            conn.close()
            return redirect(url_for('intern_dashboard', active_section='editProfile'))
        # ----------------------------------------

        # Check if new password is the same as the old one
        user = conn.execute('SELECT password FROM users WHERE id = ?', (user_id,)).fetchone()
        if user and user['password'] and check_password_hash(user['password'], new_password):
            flash('New password cannot be the same as your current password. Please choose a different one.', 'error')
            conn.close()
            return redirect(url_for('intern_dashboard', active_section='editProfile'))

    # Update name and email
    conn.execute('UPDATE users SET name = ?, email = ? WHERE id = ?',
                   (new_name, new_email, user_id))

    # Conditionally update password if a new one was entered and passed all checks
    if new_password:
        hashed_password = generate_password_hash(new_password, method='pbkdf2:sha256')
        conn.execute('UPDATE users SET password = ? WHERE id = ?',
                     (hashed_password, user_id))

    conn.commit()
    conn.close()

    session['user_name'] = new_name

    # Send confirmation email
    try:
        subject = "Your LMS Profile Has Been Updated"
        msg = Message(subject, sender=app.config['MAIL_USERNAME'], recipients=[new_email])
        msg.html = render_template('email/update_email.html', user_name=new_name, current_year=datetime.now().year)
        mail.send(msg)
    except Exception as e:
        print(f"EMAIL-ERROR on intern profile update: {e}")
        flash('Profile updated, but the confirmation email could not be sent.', 'error')
    else:
        flash('Your profile has been updated successfully!', 'success')
   
    return redirect(url_for('intern_dashboard', active_section='editProfile'))


@app.route('/set-password/<token>', methods=['GET'])
def set_password(token):
    conn = get_db_connection()
    token_data = conn.execute('SELECT * FROM password_reset_tokens WHERE token = ? AND expires_at > ?',
                              (token, datetime.now())).fetchone()
    conn.close()
    
    if token_data:
        return render_template('set_password.html', token=token)
    else:
        flash('This password reset link is invalid or has expired.', 'error')
        return redirect(url_for('login'))
    
@app.route('/process-set-password', methods=['POST'])
def process_set_password():
    token = request.form['token']
    password = request.form['password']
    confirm_password = request.form['confirm_password']
    
    password_regex = r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[\W_]).{15,}$"
    if not re.match(password_regex, password):
        return jsonify({'success': False, 'error': 'Password does not meet the requirements.'}), 400
    if password != confirm_password:
        return jsonify({'success': False, 'error': 'Passwords do not match.'}), 400

    conn = get_db_connection()
    token_data = conn.execute('SELECT * FROM password_reset_tokens WHERE token = ? AND expires_at > ?',
                              (token, datetime.now())).fetchone()
    
    if token_data:
        user_id = token_data['user_id']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        # FIX: Added the missing comma
        conn.execute('UPDATE users SET password = ?, status = ? WHERE id = ?', (hashed_password, 'active', user_id))
        conn.execute('DELETE FROM password_reset_tokens WHERE token = ?', (token,))
        conn.commit()
        conn.close()
        
        flash('Password successfully set! You can now log in.', 'success')
        return jsonify({'success': True, 'redirect_url': url_for('login')})
    else:
        return jsonify({'success': False, 'error': 'This link is invalid or has expired.'}), 400

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/request-password-reset', methods=['POST'])
def request_password_reset():

    template_path = os.path.join(app.root_path, 'templates', 'email', 'set-password-email.html')
    print(f"DEBUG: Flask is looking for the template at this exact path: {template_path}")
    print(f"DEBUG: Does the file exist at that path? {os.path.exists(template_path)}")

    email = request.form.get('email')
    if not email:
        return jsonify({'success': False, 'message': 'Email field cannot be empty.'})

    conn = get_db_connection()
    user = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close() # Close connection early if user not found

    if user:
        try:
            # Re-open connection to write the token
            conn = get_db_connection()
            token = secrets.token_urlsafe(20)
            expires_at = datetime.now() + timedelta(hours=1)
            
            cursor = conn.cursor()
            cursor.execute('INSERT INTO password_reset_tokens (user_id, token, expires_at) VALUES (?, ?, ?)',
                         (user['id'], token, expires_at))
            conn.commit()
            
            reset_link = url_for('set_password', token=token, _external=True)
            msg = Message('Password Reset Request for LMS', 
                          sender=app.config['MAIL_USERNAME'], 
                          recipients=[email])
            msg.html = render_template('email/set-password-email.html', name=user['name'], reset_link=reset_link)
            mail.send(msg)
            
            return jsonify({'success': True, 'message': f'A password reset link has been sent to {email}.'})

        except Exception as e:
            print(f"ERROR in request_password_reset: {e}")
            return jsonify({'success': False, 'message': 'An error occurred. Please try again.'})
        finally:
            if conn:
                conn.close()
    else:
        # User not found
        return jsonify({'success': False, 'message': 'No account is registered with this email address.'})


# Route to handle the course upload form
@app.route('/upload_course', methods=['POST'])
def upload_course():
    if 'user_role' not in session or session['user_role'] != 'admin':
        return redirect(url_for('login'))

    name = request.form['name']
    description = request.form['description']
    file = request.files['course_file']

    if file and file.filename != '':
        course_folder_name = secure_filename(name)
        course_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], course_folder_name)
        os.makedirs(course_folder_path, exist_ok=True)

        original_filename = secure_filename(file.filename)
        file_path = os.path.join(course_folder_path, original_filename)
        file.save(file_path)

        pdf_path = file_path
        pdf_filename = original_filename

        # Convert docx/pptx if necessary using CloudConvert API
        if original_filename.lower().endswith(('.docx', '.pptx')):
            try:
                cloudconvert.configure(api_key= "eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJhdWQiOiIxIiwianRpIjoiYWIzNTU2NjU5YmRmNzE4ZTU1YjQ0MjIyMTAzODJmNTczNWYzNTExZWU0MzFhOWFlNmM0MzE5NDhhZDNmODg3YjE4ZjYyMzc1ZDU3YjMzMGQiLCJpYXQiOjE3NTczMjkwNDkuNTk2MjU4LCJuYmYiOjE3NTczMjkwNDkuNTk2MjU5LCJleHAiOjQ5MTMwMDI2NDkuNTkxMjg3LCJzdWIiOiI3Mjg1NDY0MiIsInNjb3BlcyI6W119.ZNNxJCYHfSzSJwZZcuh5DwUANtu3XvskgG6WS1OeUW7q2j44sysrkBnR5aR3cNAfbacLLsNljO0QuFXRFDsniwJHsWsNoYAatd6UA4t9I5PMzmj1EGGZesATduaozpkZW5h-4q84LHI_XdXnnoSN7mbX-2FZcsl9Dhmq0MhCxNKUN_0agdPHsWor06Lwi2ut2Lex-z_38eufkshgbWZDOB2NsPT0uQkjcE0lBdk7s9coNNmCHvLRN98VoGlaVSCCz1Nxk7NUDvwXC2JgaW8O-1hbsyW21QCa4sSFI6i4CuMHEkXCAlIhwkWvsGWSLMfLzLSpv_lrhzUHFwF_y9ucwS64878-60KwqRwPF61bxPs29_HHwDUFf3sRcCEI0BXqZTWLp_fNcEXk434NFYqdgHktcSe45hNBtMEKtULvapdEXooR6jm-Tl6vKfMgUSOgubh-KakVlYAFWnGJAp1J8wwqWK0tmKOWvzCq_jTYTHV6YEcqMMJ2ywjprz08feBFxX8piZrwjnFXJp4J5Y7Gs9GMCZkiW1EjX8xONOYCRcMHs2eV9CCOjRf5Rb4EZHxg31hWmrkPZTDZ2QwqPmbfOSUASHkNfsgU_C5IXryltp9EX62cDP6Czl5ylW9fh48zwDFdCPS5XzxA666dunMEk1nMXs8u-6VL97WCJkN40zw")
                job = cloudconvert.Job.create(payload={"tasks": {'upload-file': {'operation': 'import/upload'},'convert-file': {'operation': 'convert', 'input': 'upload-file', 'output_format': 'pdf'},'export-file': {'operation': 'export/url', 'input': 'convert-file'}}})
                upload_task = cloudconvert.Task.find(id=job['tasks'][0]['id'])
                cloudconvert.Task.upload(file_name=file_path, task=upload_task)
                job = cloudconvert.Job.wait(id=job['id'])
                exported_url_task = cloudconvert.Task.wait(id=job["tasks"][2]["id"])
                converted_file = exported_url_task.get("result").get("files")[0]
                pdf_filename = converted_file['filename']
                pdf_path = os.path.join(course_folder_path, pdf_filename)
                cloudconvert.download(filename=pdf_path, url=converted_file['url'])
            except Exception as e:
                flash(f'Error converting file: {e}', 'error')
                return redirect(url_for('admin_dashboard'))
        
        page_images, quiz_data, raw_text = [], [], ""
        if pdf_filename.lower().endswith('.pdf'):
            try:
                doc = fitz.open(pdf_path)
                for page_num, page in enumerate(doc):
                    pix = page.get_pixmap()
                    image_name = f"{os.path.splitext(pdf_filename)[0]}_page_{page_num + 1}.png"
                    pix.save(os.path.join(course_folder_path, image_name))
                    page_images.append(os.path.join(course_folder_name, image_name))
                    raw_text += page.get_text()

                # AI is called here, ONCE during upload
                prompt = f"""
                You are a helpful assistant that creates quizzes from educational text.
                Analyze the following text and generate a large question bank.

                **Instructions:**
                1.  Create **at least 20** questions, but more if the text supports it.
                2.  Include a mix of multiple-choice and true/false questions.
                3.  The output **must** be a single, valid JSON object.
                4.  The JSON object must have one top-level key: "quiz".
                5.  The value of "quiz" must be a list of 20 or more question objects.
                6.  Each question object must have these keys: "type" (string), "question" (string), "options" (list of strings), and "answer" (string). For true/false questions, the "options" list should be ["True", "False"].

                Text to analyze:
                ---
                {raw_text[:8000]} 
                ---
                """
                response = model.generate_content(prompt)
                response = model.generate_content(prompt)
                print("----------- RAW AI RESPONSE -----------")
                print(response.text)
                print("---------------------------------------")
                cleaned_response = response.text.strip().replace("```json", "").replace("```", "")
                quiz_data = json.loads(cleaned_response).get("quiz", [])
            except Exception as e:
                print(f"AI or PDF Processing Error: {e}")

        # The quiz_data is now saved with the course
        structured_data = {"pages": page_images, "quiz": quiz_data}
        extracted_text_json = json.dumps(structured_data)
        db_file_path = os.path.join(course_folder_name, pdf_filename)
        conn = get_db_connection()
        conn.execute('INSERT INTO courses (name, description, filename, extracted_text) VALUES (?, ?, ?, ?)',
                     (name, description, db_file_path, extracted_text_json))
        conn.commit()
        conn.close()
        flash('Course processed and uploaded successfully!', 'success')
    else:
        flash('No file selected for upload.', 'error')
    return redirect(url_for('admin_dashboard'))

# Route to serve/download the uploaded files
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    if 'user_id' not in session: # Basic security to ensure user is logged in
        return redirect(url_for('login'))
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# In app.py
# Make sure to add 'import shutil' at the top of the file

@app.route('/delete_course/<int:course_id>', methods=['POST'])
def delete_course(course_id):
    if 'user_role' not in session or session['user_role'] != 'admin':
        return redirect(url_for('login'))

    conn = get_db_connection()
    # Get the course name to find its folder
    course = conn.execute('SELECT name FROM courses WHERE id = ?', (course_id,)).fetchone()
    
    if course:
        # Construct the folder path
        course_folder_name = secure_filename(course['name'])
        course_folder_path = os.path.join(app.config['UPLOAD_FOLDER'], course_folder_name)

        # Delete the entire folder and its contents
        if os.path.exists(course_folder_path):
            try:
                shutil.rmtree(course_folder_path)
            except OSError as e:
                print(f"Error deleting folder {course_folder_path}: {e}")
                flash(f'Could not completely remove course files. Please check the server.', 'error')

        # Delete the associated quiz attempts first
        conn.execute('DELETE FROM quiz_attempts WHERE course_id = ?', (course_id,))
        # Then delete the course record from the database
        conn.execute('DELETE FROM courses WHERE id = ?', (course_id,))
        conn.commit()
        flash('Course and all associated data removed successfully!', 'success')
    else:
        flash('Course not found.', 'error')
        
    conn.close()
    return redirect(url_for('admin_dashboard', active_section='viewCoursesSection'))


@app.route('/edit_course/<int:course_id>', methods=['POST'])
def edit_course(course_id):
    if 'user_role' not in session or session['user_role'] != 'admin':
        return redirect(url_for('login'))

    name = request.form['name']
    description = request.form['description']
    file = request.files.get('course_file') # Use .get() to make file optional

    conn = get_db_connection()

    if file and file.filename != '':
        # If a new file is uploaded, replace the old one
        old_course = conn.execute('SELECT filename FROM courses WHERE id = ?', (course_id,)).fetchone()
        
        # Delete old file
        if old_course:
            try:
                os.remove(os.path.join(app.config['UPLOAD_FOLDER'], old_course['filename']))
            except FileNotFoundError:
                print(f"Old file not found during edit: {old_course['filename']}")

        # Save new file and update record with new filename
        filename = secure_filename(file.filename)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        conn.execute('UPDATE courses SET name = ?, description = ?, filename = ? WHERE id = ?',
                     (name, description, filename, course_id))
    else:
        # If no new file, just update the name and description
        conn.execute('UPDATE courses SET name = ?, description = ? WHERE id = ?',
                     (name, description, course_id))

    conn.commit()
    conn.close()
    flash('Course updated successfully!', 'success')
    return redirect(url_for('admin_dashboard'))

@app.route('/get_course_content/<int:course_id>')
def get_course_content(course_id):
    if 'user_role' not in session or session['user_role'] != 'admin':
        return jsonify({'error': 'Permission denied'}), 403

    conn = get_db_connection()
    course = conn.execute('SELECT filename FROM courses WHERE id = ?', (course_id,)).fetchone()
    conn.close()

    if not course:
        return jsonify({'error': 'Course not found'}), 404

    filename = course['filename']
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    
    # Default response
    response_data = {'filename': filename, 'file_type': 'other'}

    try:
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
            response_data['file_type'] = 'image'
        elif filename.lower().endswith('.pdf'):
            response_data['file_type'] = 'pdf'
        elif filename.lower().endswith('.txt'):
            response_data['file_type'] = 'text'
            # Read the content only for text files
            with open(file_path, 'r', encoding='utf-8') as f:
                response_data['content'] = f.read()

    except FileNotFoundError:
        return jsonify({'error': 'File not found on server'}), 404
    except Exception as e:
        print(f"Error reading file: {e}")
        return jsonify({'error': 'Could not read file content'}), 500
        
    return jsonify(response_data)


@app.route('/learn/<int:course_id>')
def learn(course_id):
    if 'user_role' not in session or session['user_role'] != 'intern':
        return redirect(url_for('login'))

    conn = get_db_connection()
    course = conn.execute('SELECT * FROM courses WHERE id = ?', (course_id,)).fetchone()
    conn.close()

    if not course or not course['extracted_text']:
        flash('Course materials not found or could not be processed.', 'error')
        return redirect(url_for('intern_dashboard'))

    try:
        learning_data = json.loads(course['extracted_text'])
    except json.JSONDecodeError:
        learning_data = {"pages": [], "quiz": []}
    
    return render_template('learn.html', course=course, learning_data=learning_data)


    # --- QUIZ ROUTES ---
@app.route('/start_quiz/<int:course_id>')
def start_quiz(course_id):
    if 'user_role' not in session or session['user_role'] != 'intern':
        return jsonify({'error': 'Permission denied'}), 403

    conn = get_db_connection()
    course = conn.execute('SELECT extracted_text FROM courses WHERE id = ?', (course_id,)).fetchone()
    if not course:
        conn.close()
        return jsonify({'error': 'Course not found'}), 404
    
    # Load the full question bank from the database
    full_question_bank = json.loads(course['extracted_text']).get('quiz', [])

    if len(full_question_bank) < 10:
        return jsonify({'error': f'Not enough questions in the bank to generate a 10-question quiz. Please contact an admin.'}), 400

    # Randomly select 10 questions from the bank without replacement
    randomized_quiz = random.sample(full_question_bank, 10)

    # Create a new quiz attempt record with the randomized 10-question quiz
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO quiz_attempts (user_id, course_id, quiz_data, attempted_at, total_questions) VALUES (?, ?, ?, ?, ?)',
        (session['user_id'], course_id, json.dumps(randomized_quiz), datetime.now(), 10) # Total questions is now 10
    )
    attempt_id = cursor.lastrowid
    conn.commit()
    conn.close()

    # Return the newly generated 10-question quiz to the intern
    return jsonify({'quiz': randomized_quiz, 'attempt_id': attempt_id})


@app.route('/submit_quiz/<int:attempt_id>', methods=['POST'])
def submit_quiz(attempt_id):
    if 'user_role' not in session or session['user_role'] != 'intern':
        return jsonify({'error': 'Permission denied'}), 403

    user_answers = request.json.get('answers')
    
    conn = get_db_connection()
    attempt = conn.execute(
        'SELECT * FROM quiz_attempts WHERE id = ? AND user_id = ?',
        (attempt_id, session['user_id'])
    ).fetchone()

    if not attempt:
        conn.close()
        return jsonify({'error': 'Quiz attempt not found.'}), 404

    correct_answers = json.loads(attempt['quiz_data'])
    score = 0
    total = len(correct_answers)

    for i, question_data in enumerate(correct_answers):
        if i < len(user_answers) and user_answers[i] == question_data['answer']:
            score += 1
            
    # --- NEW: Determine Pass/Fail Status ---
    pass_threshold = 0.7 # 70% to pass
    status = "Pass" if (score / total) >= pass_threshold else "Fail"
    # ----------------------------------------

    # --- UPDATED: Save the score, total, AND status ---
    conn.execute('UPDATE quiz_attempts SET score = ?, total_questions = ?, status = ? WHERE id = ?', 
                 (score, total, status, attempt_id))
    conn.commit()
    conn.close()

    # Return the status to the frontend
    return jsonify({'score': score, 'total': total, 'status': status})


    # In app.py

@app.route('/contact_admin', methods=['POST'])
def contact_admin():
    # Ensure the user is a logged-in intern
    if 'user_role' not in session or session['user_role'] != 'intern':
        return redirect(url_for('login'))

    message_body = request.form.get('message_body')
    intern_id = session['user_id']

    conn = get_db_connection()
    
    # Get the sender's details
    intern = conn.execute('SELECT name, email FROM users WHERE id = ?', (intern_id,)).fetchone()
    
    # Find all admin email addresses
    admins = conn.execute("SELECT email FROM users WHERE role = 'admin' AND status = 'active'").fetchall()
    conn.close()

    if not admins:
        flash('Could not send message. No administrators found.', 'error')
        return redirect(url_for('intern_dashboard', active_section='contactAdmin'))

    # Create a list of recipient emails
    admin_emails = [admin['email'] for admin in admins]

    try:
        msg = Message(
            subject=f"Message from Intern: {intern['name']}",
            sender=app.config['MAIL_USERNAME'],
            recipients=admin_emails
        )
        msg.body = (
            f"You have received a new message from an intern.\n\n"
            f"From: {intern['name']}\n"
            f"Email: {intern['email']}\n\n"
            f"Message:\n--------------------\n{message_body}\n--------------------"
        )
        mail.send(msg)
        flash('Your message has been sent to the administrators.', 'success')
    except Exception as e:
        print(f"EMAIL-ERROR in contact_admin: {e}")
        flash('An error occurred while sending your message. Please try again later.', 'error')

    return redirect(url_for('intern_dashboard', active_section='contactAdmin'))

@app.route('/admin/get_intern_scores/<int:intern_id>')
def get_intern_scores(intern_id):
    # Ensure the user is a logged-in admin
    if 'user_role' not in session or session['user_role'] != 'admin':
        return jsonify({'error': 'Permission denied'}), 403

    conn = get_db_connection()
    
    # Get the intern's name for the modal title
    intern = conn.execute('SELECT name FROM users WHERE id = ?', (intern_id,)).fetchone()
    if not intern:
        conn.close()
        return jsonify({'error': 'Intern not found'}), 404

    # Get the intern's quiz history
    quiz_history = conn.execute("""
        SELECT c.name AS course_name, qa.score, qa.total_questions
        FROM quiz_attempts qa
        JOIN courses c ON qa.course_id = c.id
        WHERE qa.user_id = ? AND qa.score IS NOT NULL
        ORDER BY qa.attempted_at DESC
    """, (intern_id,)).fetchall()
    
    conn.close()

    # Convert the database rows to a list of dictionaries
    scores_list = [dict(row) for row in quiz_history]
    
    return jsonify({
        'intern_name': intern['name'],
        'scores': scores_list
    })


# --- RUN THE APP ---
if __name__ == '__main__':
    app.run(debug=True) 
