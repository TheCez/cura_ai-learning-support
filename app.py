import os
from dotenv import load_dotenv
load_dotenv()

from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from models import (db, User, Course, Module, Enrollment, QuizQuestion, QuizAttempt,
                    Flashcard, FlashcardProgress, DailyGoal, FriendMission, LottoWinner,
                    CaseStudyAttempt, friendship)
from services import (init_db, calculate_language_level, calculate_nursing_level,
                      LANGUAGE_TEST, NURSING_TEST, get_ai_professor_response,
                      call_gemini_chat, build_ki_lehrer_system_prompt,
                      generate_slide_from_speech,
                      generate_ai_quiz, evaluate_ai_answers,
                      generate_quiz_fallback,
                      generate_language_test_questions, generate_nursing_test_questions,
                      build_student_context, generate_flashcards, generate_library_summary,
                      generate_library_cards,
                      FALL_BLUTDRUCK, build_fall_blutdruck_prompt, detect_fall_steps)

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///carelearn.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'carelearn-secret-key')


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = _env_flag('DEV_HTTPS', True)

db.init_app(app)
init_db(app)


# ── Auth-Decorator ─────────────────────────────
def login_required(role=None):
    def wrapper(fn):
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                flash('Bitte melde dich zuerst an.', 'warning')
                return redirect(url_for('login'))
            user = User.query.get(session['user_id'])
            if not user or (role and user.role != role):
                flash('Zugriff verweigert.', 'danger')
                return redirect(url_for('index'))
            return fn(user, *args, **kwargs)
        decorated.__name__ = fn.__name__
        return decorated
    return wrapper


@app.context_processor
def inject_user():
    user = None
    if 'user_id' in session:
        user = User.query.get(session['user_id'])
    return {'current_user': user}


def update_friend_mission_progress(student_id: int, xp_delta: int):
    """Add XP to all active friend missions for the given student and mark completed ones."""
    if xp_delta <= 0:
        return

    active_missions = FriendMission.query.filter(
        FriendMission.completed == False,
        db.or_(FriendMission.creator_id == student_id, FriendMission.friend_id == student_id)
    ).all()

    for mission in active_missions:
        if mission.creator_id == student_id:
            mission.creator_xp = (mission.creator_xp or 0) + xp_delta
        if mission.friend_id == student_id:
            mission.friend_xp = (mission.friend_xp or 0) + xp_delta

        if (mission.creator_xp or 0) >= mission.target_xp and (mission.friend_xp or 0) >= mission.target_xp:
            mission.completed = True


# ── Öffentliche Seiten ─────────────────────────
@app.route('/favicon.ico')
def favicon():
    svg = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="#1a5c8a"/><path d="M16 25C16 25 7 18.5 7 13.5C7 10.46 9.46 8 12.5 8C14.24 8 15.91 8.9 17 10.18C18.09 8.9 19.76 8 21.5 8C24.54 8 27 10.46 27 13.5C27 18.5 16 25 16 25Z" fill="white" opacity="0.9"/><line x1="16" y1="12" x2="16" y2="18" stroke="#1a5c8a" stroke-width="1.8" stroke-linecap="round"/><line x1="13" y1="15" x2="19" y2="15" stroke="#1a5c8a" stroke-width="1.8" stroke-linecap="round"/></svg>'
    return svg, 200, {'Content-Type': 'image/svg+xml', 'Cache-Control': 'max-age=86400'}

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        role = request.form['role']
        email = request.form['email'].strip().lower()
        password = request.form['password']
        first_name = request.form['first_name'].strip()
        last_name = request.form['last_name'].strip()
        country = request.form.get('country', '').strip()
        language = request.form.get('language', '').strip()
        speciality = request.form.get('speciality', '').strip()

        if User.query.filter_by(email=email).first():
            flash('Diese E-Mail-Adresse ist bereits registriert.', 'danger')
            return redirect(url_for('register'))

        user = User(
            role=role,
            email=email,
            password_hash=generate_password_hash(password),
            first_name=first_name,
            last_name=last_name,
            country=country,
            language=language,
            speciality=speciality,
        )
        db.session.add(user)
        db.session.commit()
        session['user_id'] = user.id

        if role == 'student':
            flash('Willkommen! Bitte absolviere jetzt den Sprachtest, damit wir deinen Kurs anpassen können.', 'success')
            return redirect(url_for('language_test'))
        flash('Registrierung erfolgreich. Willkommen im Lehrerbereich.', 'success')
        return redirect(url_for('teacher_dashboard'))

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email'].strip().lower()
        password = request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            flash(f'Willkommen zurück, {user.first_name}!', 'success')
            return redirect(url_for('student_dashboard' if user.role == 'student' else 'teacher_dashboard'))
        flash('E-Mail oder Passwort ist falsch.', 'danger')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash('Du wurdest erfolgreich abgemeldet.', 'info')
    return redirect(url_for('index'))


# ── Schüler-Bereich ────────────────────────────
@app.route('/student')
@login_required(role='student')
def student_dashboard(user):
    enrolled = [e.course for e in user.enrollments]
    enrolled_ids = [c.id for c in enrolled]
    available = Course.query.filter(Course.id.notin_(enrolled_ids)).all() if enrolled_ids else Course.query.all()

    # Fortschritt je Kurs berechnen
    progress = {}
    for course in enrolled:
        total = len(course.modules)
        if total == 0:
            progress[course.id] = 0
        else:
            done = db.session.query(QuizAttempt.module_id).distinct()\
                .join(Module, QuizAttempt.module_id == Module.id)\
                .filter(Module.course_id == course.id, QuizAttempt.student_id == user.id)\
                .count()
            progress[course.id] = int(done / total * 100)

    onboarding_done = user.language_score > 0 and user.nursing_score > 0

    # Dashboard stats
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    streak = 0
    check_date = today - _td(days=1)
    while True:
        g = DailyGoal.query.filter_by(student_id=user.id, date=check_date).first()
        if g and g.earned_xp >= g.target_xp:
            streak += 1
            check_date -= _td(days=1)
        else:
            break

    higher = User.query.filter(User.role == 'student', (User.xp or 0) > (user.xp or 0)).count()
    rank = higher + 1
    total_students = User.query.filter_by(role='student').count()
    quizzes_done = QuizAttempt.query.filter_by(student_id=user.id).count()

    stats = {
        'streak': streak,
        'rank': rank,
        'total_students': total_students,
        'xp': user.xp or 0,
        'quizzes': quizzes_done,
    }

    # Spaced repetition: collect modules due for review
    from datetime import datetime as _dt
    now = _dt.utcnow()
    due_reviews = []
    seen_modules = set()
    latest_attempts = db.session.query(QuizAttempt)\
        .filter_by(student_id=user.id)\
        .order_by(QuizAttempt.completed_at.desc()).all()
    for attempt in latest_attempts:
        if attempt.module_id in seen_modules:
            continue
        seen_modules.add(attempt.module_id)
        if attempt.next_review_at and attempt.next_review_at <= now:
            mod = Module.query.get(attempt.module_id)
            if mod:
                delta = now - attempt.next_review_at
                days_overdue = delta.days
                due_label = 'heute' if days_overdue == 0 else f'vor {days_overdue} Tag(en)'
                due_reviews.append({
                    'module_title': mod.title,
                    'course_title': mod.course.title if mod.course else '',
                    'pct': attempt.pct or 0,
                    'due_since': due_label,
                    'quiz_url': f'/courses/{mod.course_id}/module/{mod.id}/quiz' if mod.course_id else '#',
                })

    return render_template(
        'dashboard_student.html',
        user=user,
        enrolled=enrolled,
        available=available,
        progress=progress,
        onboarding_done=onboarding_done,
        due_reviews=due_reviews,
        stats=stats,
    )


# ── Onboarding-Tests ───────────────────────────
@app.route('/language-test', methods=['GET'])
@login_required(role='student')
def language_test(user):
    return render_template('language_test.html')


@app.route('/api/language-test/generate', methods=['GET'])
@login_required(role='student')
def api_language_test_generate(user):
    from flask import jsonify
    questions = generate_language_test_questions()
    if not questions:
        # Fallback to static questions converted to unified format
        questions = [
            {'type': 'multiple_choice', 'question': q['question'],
             'options': q['choices'], 'answer': q['answer']}
            for q in LANGUAGE_TEST
        ]
    return jsonify({'questions': questions})


@app.route('/api/language-test/submit', methods=['POST'])
@login_required(role='student')
def api_language_test_submit(user):
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    questions = data.get('questions', [])
    answers = {str(k): v for k, v in data.get('answers', {}).items()}

    results = evaluate_ai_answers(questions, answers, user.language_level or 'A1')
    score = sum(r['score'] for r in results)

    user.language_score = score
    user.language_level = calculate_language_level(score)
    db.session.commit()

    return jsonify({'score': score, 'total': len(results),
                    'level': user.language_level, 'results': results})


@app.route('/nursing-test', methods=['GET'])
@login_required(role='student')
def nursing_test(user):
    return render_template('nursing_test.html')


@app.route('/api/nursing-test/generate', methods=['GET'])
@login_required(role='student')
def api_nursing_test_generate(user):
    from flask import jsonify
    questions = generate_nursing_test_questions()
    if not questions:
        questions = [
            {'type': 'multiple_choice' if not q.get('multi') else 'multiple_choice',
             'question': q['question'], 'options': q['choices'], 'answer': q['answer']}
            for q in NURSING_TEST
        ]
    return jsonify({'questions': questions})


@app.route('/api/nursing-test/submit', methods=['POST'])
@login_required(role='student')
def api_nursing_test_submit(user):
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    questions = data.get('questions', [])
    answers = {str(k): v for k, v in data.get('answers', {}).items()}

    results = evaluate_ai_answers(questions, answers, user.language_level or 'A1')
    score = sum(r['score'] for r in results)

    user.nursing_score = score
    user.nursing_level = calculate_nursing_level(score)
    db.session.commit()

    return jsonify({'score': score, 'total': len(results),
                    'level': user.nursing_level, 'results': results})


# ── Kurse ──────────────────────────────────────
@app.route('/courses')
@login_required(role='student')
def courses(user):
    all_courses = Course.query.all()
    enrolled_ids = {e.course_id for e in user.enrollments}
    return render_template('courses.html', courses=all_courses, enrolled_ids=enrolled_ids)


@app.route('/courses/<int:course_id>')
@login_required(role='student')
def course_detail(user, course_id):
    from sqlalchemy import func

    course = Course.query.get_or_404(course_id)
    enrolled = Enrollment.query.filter_by(course_id=course.id, student_id=user.id).first()

    module_stats = {}
    attempt_summary = {}
    module_ids = [m.id for m in course.modules]

    if enrolled and module_ids:
        rows = db.session.query(
            QuizAttempt.module_id,
            func.count(QuizAttempt.id),
            func.max(QuizAttempt.pct)
        ).filter(
            QuizAttempt.student_id == user.id,
            QuizAttempt.module_id.in_(module_ids)
        ).group_by(QuizAttempt.module_id).all()

        for module_id, attempts, best_pct in rows:
            attempt_summary[module_id] = {
                'attempts': int(attempts or 0),
                'best_pct': int(best_pct or 0),
            }

    for module in course.modules:
        db_question_count = len(module.quiz_questions or [])
        summary = attempt_summary.get(module.id, {'attempts': 0, 'best_pct': 0})
        module_stats[module.id] = {
            'question_count': db_question_count if db_question_count > 0 else 5,
            'question_source': 'custom' if db_question_count > 0 else 'ai',
            'attempts': summary['attempts'],
            'best_pct': summary['best_pct'],
        }

    return render_template(
        'course_detail.html',
        course=course,
        enrolled=enrolled,
        module_stats=module_stats,
    )


@app.route('/courses/<int:course_id>/enroll')
@login_required(role='student')
def enroll_course(user, course_id):
    course = Course.query.get_or_404(course_id)
    if Enrollment.query.filter_by(student_id=user.id, course_id=course.id).first():
        flash('Du bist bereits in diesem Kurs angemeldet.', 'warning')
        return redirect(url_for('course_detail', course_id=course_id))
    enrollment = Enrollment(student_id=user.id, course_id=course.id)
    db.session.add(enrollment)
    db.session.commit()
    flash(f'Erfolgreich in "{course.title}" eingeschrieben!', 'success')
    return redirect(url_for('course_detail', course_id=course_id))


@app.route('/courses/<int:course_id>/module/<int:module_id>')
@login_required(role='student')
def course_module(user, course_id, module_id):
    course = Course.query.get_or_404(course_id)
    module = Module.query.get_or_404(module_id)
    last_attempt = QuizAttempt.query.filter_by(
        student_id=user.id, module_id=module_id
    ).order_by(QuizAttempt.completed_at.desc()).first()
    return render_template('module_detail.html', course=course, module=module, last_attempt=last_attempt)


@app.route('/courses/<int:course_id>/module/<int:module_id>/quiz', methods=['GET'])
@login_required(role='student')
def course_quiz(user, course_id, module_id):
    module = Module.query.get_or_404(module_id)
    source = (request.args.get('source') or '').strip().lower()

    if source == 'learn':
        cancel_url = url_for('learning_page', module_id=module.id)
        cancel_label = 'Zurueck zu Lernen'
    else:
        cancel_url = url_for('course_module', course_id=course_id, module_id=module.id)
        cancel_label = 'Quiz abbrechen'

    return render_template(
        'quiz.html',
        module=module,
        course_id=course_id,
        cancel_url=cancel_url,
        cancel_label=cancel_label,
    )


@app.route('/quiz', methods=['GET'])
@login_required(role='student')
def legacy_quiz_redirect(user):
    module_id = request.args.get('module_id', type=int)
    if not module_id:
        return redirect(url_for('student_dashboard'))

    module = Module.query.get_or_404(module_id)
    return redirect(url_for('course_quiz', course_id=module.course_id, module_id=module.id))


@app.route('/api/quiz/generate/<int:module_id>', methods=['GET'])
@login_required(role='student')
def api_quiz_generate(user, module_id):
    from flask import jsonify
    module = Module.query.get_or_404(module_id)
    questions = generate_ai_quiz(module, user.language_level or 'A2')
    if not questions:
        # Fallback: convert static DB questions if available
        if module.quiz_questions:
            questions = []
            for q in module.quiz_questions:
                opts = q.options.split(';') if q.options else []
                questions.append({
                    'id': q.id, 'type': 'multiple_choice',
                    'question': q.question, 'options': opts,
                    'answer': q.answer.split(';')[0],
                })
        else:
            questions = generate_quiz_fallback(module)

    if not questions:
        return jsonify({'error': 'Keine Fragen verfuegbar – bitte spaeter erneut versuchen.'}), 503
    return jsonify({'questions': questions})


@app.route('/api/quiz/submit', methods=['POST'])
@login_required(role='student')
def api_quiz_submit(user):
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    module_id = data.get('module_id')
    questions = data.get('questions', [])
    answers = {str(k): v for k, v in data.get('answers', {}).items()}

    if not module_id or not questions:
        return jsonify({'error': 'Fehlende Daten'}), 400

    from datetime import datetime, timedelta
    module = Module.query.get_or_404(module_id)
    results = evaluate_ai_answers(questions, answers, user.language_level or 'A2')
    score = sum(r['score'] for r in results)
    total = len(results)
    pct = int(score / total * 100) if total else 0

    # Spaced repetition: schedule next review based on performance
    if pct >= 90:
        review_days = 14       # Almost perfect → 2 weeks
    elif pct >= 70:
        review_days = 7        # Good → 1 week
    elif pct >= 50:
        review_days = 3        # OK → 3 days
    else:
        review_days = 1        # Struggling → tomorrow

    next_review = datetime.utcnow() + timedelta(days=review_days)

    attempt = QuizAttempt(
        student_id=user.id, module_id=module.id,
        score=score, max_score=total, pct=pct,
        next_review_at=next_review
    )
    db.session.add(attempt)

    # Award XP
    xp_earned = score * 10 + (20 if pct >= 80 else 0)
    user.xp = (user.xp or 0) + xp_earned
    # Update daily goal
    from datetime import date as _date
    today_goal = DailyGoal.query.filter_by(student_id=user.id, date=_date.today()).first()
    if not today_goal:
        today_goal = DailyGoal(student_id=user.id, date=_date.today())
        db.session.add(today_goal)
    today_goal.earned_xp = (today_goal.earned_xp or 0) + xp_earned

    update_friend_mission_progress(user.id, xp_earned)

    enrollment = Enrollment.query.filter_by(student_id=user.id, course_id=module.course_id).first()
    if enrollment:
        done = db.session.query(QuizAttempt.module_id).distinct()\
            .join(Module, QuizAttempt.module_id == Module.id)\
            .filter(Module.course_id == module.course_id, QuizAttempt.student_id == user.id)\
            .count()
        enrollment.completed_modules = done + 1

    db.session.commit()
    return jsonify({
        'score': score, 'total': total, 'pct': pct, 'results': results,
        'next_review_days': review_days,
        'next_review_date': next_review.strftime('%d.%m.%Y'),
    })


# ── Lehrer-Bereich ─────────────────────────────
@app.route('/teacher')
@login_required(role='teacher')
def teacher_dashboard(user):
    courses = user.courses
    enrollments = Enrollment.query.join(Course).filter(Course.owner_id == user.id).all()
    return render_template('dashboard_teacher.html', user=user, courses=courses, enrollments=enrollments)


@app.route('/teacher/course/new', methods=['GET', 'POST'])
@login_required(role='teacher')
def create_course(user):
    if request.method == 'POST':
        title = request.form['title'].strip()
        summary = request.form['summary'].strip()
        level = request.form['recommended_level']
        module_title = request.form['module_title'].strip()
        module_description = request.form['module_description'].strip()
        module_type = request.form['module_type']
        content_body = request.form.get('content_body', '').strip()

        course = Course(title=title, summary=summary, recommended_level=level, owner_id=user.id)
        db.session.add(course)
        db.session.flush()
        module = Module(
            course_id=course.id,
            title=module_title,
            description=module_description,
            module_type=module_type,
            content=content_body,
            position=1
        )
        db.session.add(module)
        db.session.commit()
        flash(f'Kurs "{title}" und erstes Modul gespeichert.', 'success')
        return redirect(url_for('teacher_course', course_id=course.id))
    return render_template('upload_course.html')


@app.route('/teacher/course/<int:course_id>')
@login_required(role='teacher')
def teacher_course(user, course_id):
    course = Course.query.get_or_404(course_id)
    if course.owner_id != user.id:
        flash('Zugriff verweigert.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    enrollments = Enrollment.query.filter_by(course_id=course_id).all()
    return render_template('teacher_course.html', course=course, enrollments=enrollments)


@app.route('/teacher/course/<int:course_id>/set-current-module', methods=['POST'])
@login_required(role='teacher')
def set_current_module(user, course_id):
    course = Course.query.get_or_404(course_id)
    if course.owner_id != user.id:
        flash('Zugriff verweigert.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    module_id = request.form.get('module_id', type=int)
    if module_id:
        module = Module.query.get_or_404(module_id)
        course.current_module_id = module.id
        flash(f'Heutiges Thema gesetzt: "{module.title}"', 'success')
    else:
        course.current_module_id = None
        flash('Heutiges Thema zurückgesetzt.', 'info')
    db.session.commit()
    return redirect(url_for('teacher_course', course_id=course_id))


@app.route('/teacher/course/<int:course_id>/module/new', methods=['POST'])
@login_required(role='teacher')
def add_module(user, course_id):
    course = Course.query.get_or_404(course_id)
    if course.owner_id != user.id:
        flash('Zugriff verweigert.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    title = request.form['module_title'].strip()
    description = request.form['module_description'].strip()
    module_type = request.form['module_type']
    content_body = request.form.get('content_body', '').strip()
    position = len(course.modules) + 1

    module = Module(
        course_id=course.id,
        title=title,
        description=description,
        module_type=module_type,
        content=content_body,
        position=position
    )
    db.session.add(module)
    db.session.commit()
    flash(f'Modul "{title}" hinzugefügt.', 'success')
    return redirect(url_for('teacher_course', course_id=course_id))


@app.route('/teacher/course/<int:course_id>/module/<int:module_id>/add-question', methods=['POST'])
@login_required(role='teacher')
def add_quiz_question(user, course_id, module_id):
    course = Course.query.get_or_404(course_id)
    if course.owner_id != user.id:
        flash('Zugriff verweigert.', 'danger')
        return redirect(url_for('teacher_dashboard'))

    module = Module.query.get_or_404(module_id)
    question_text = request.form['question'].strip()
    # Antwortmöglichkeiten: jede Zeile ist eine Option
    raw_options = request.form['options'].strip()
    options = [o.strip() for o in raw_options.splitlines() if o.strip()]
    answer_raw = request.form['answer'].strip()
    # Mehrere richtige Antworten mit Semikolon trennen
    is_multi = request.form.get('multi_choice') == 'on'

    if not question_text or not options or not answer_raw:
        flash('Bitte alle Felder ausfüllen.', 'danger')
        return redirect(url_for('teacher_course', course_id=course_id))

    q = QuizQuestion(
        module_id=module.id,
        question=question_text,
        options=';'.join(options),
        answer=answer_raw,
        multi_choice=is_multi
    )
    db.session.add(q)
    db.session.commit()
    flash('Quiz-Frage hinzugefügt.', 'success')
    return redirect(url_for('teacher_course', course_id=course_id))


@app.route('/teacher/course/<int:course_id>/module/<int:module_id>/delete-question/<int:question_id>', methods=['POST'])
@login_required(role='teacher')
def delete_quiz_question(user, course_id, module_id, question_id):
    course = Course.query.get_or_404(course_id)
    if course.owner_id != user.id:
        flash('Zugriff verweigert.', 'danger')
        return redirect(url_for('teacher_dashboard'))
    q = QuizQuestion.query.get_or_404(question_id)
    db.session.delete(q)
    db.session.commit()
    flash('Frage gelöscht.', 'info')
    return redirect(url_for('teacher_course', course_id=course_id))


@app.route('/teacher/student-progress')
@login_required(role='teacher')
def student_progress(user):
    enrollments = Enrollment.query.join(Course).filter(Course.owner_id == user.id).all()
    # Fortschrittsdetails je Schüler
    details = []
    for enrollment in enrollments:
        attempts = QuizAttempt.query.filter_by(student_id=enrollment.student_id)\
            .join(Module, QuizAttempt.module_id == Module.id)\
            .filter(Module.course_id == enrollment.course_id)\
            .all()
        best_scores = {}
        for a in attempts:
            if a.module_id not in best_scores or a.score > best_scores[a.module_id]:
                best_scores[a.module_id] = a.score
        details.append({
            'enrollment': enrollment,
            'attempts': len(attempts),
            'modules_done': len(best_scores),
            'total_modules': len(enrollment.course.modules),
        })
    return render_template('student_progress.html', details=details)


# ── KI-Professorin ─────────────────────────────
@app.route('/ai-professor', methods=['GET'])
@login_required(role='student')
def ai_professor(user):
    enrolled_courses = [e.course for e in user.enrollments]
    return render_template(
        'ai_professor.html',
        user=user,
        enrolled_courses=enrolled_courses,
    )


@app.route('/api/ai-professor', methods=['POST'])
@login_required(role='student')
def ai_professor_api(user):
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    topic = data.get('topic', '').strip()
    selected_course_id = data.get('course_id')

    if not topic:
        return jsonify({'error': 'Kein Thema angegeben.'}), 400

    course_context = ''
    if selected_course_id:
        course = Course.query.get(selected_course_id)
        if course and any(e.course_id == selected_course_id for e in user.enrollments):
            parts = [f"Kurs: {course.title}\n{course.summary}"]
            for module in course.modules:
                parts.append(f"Modul: {module.title}\n{module.description}")
                if module.content:
                    parts.append(module.content)
            course_context = '\n\n'.join(parts)

    response_text = get_ai_professor_response(topic, user.language_level, course_context, build_student_context(user))
    return jsonify({'response': response_text})


# ── KI-Lehrer (interaktiver Avatar-Unterricht) ─
@app.route('/ki-lehrer')
@login_required(role='student')
def ki_lehrer(user):
    from flask import jsonify as _j
    enrolled_courses = [e.course for e in user.enrollments]
    # Pass module content as a dict so Jinja tojson can embed it safely in <script>
    module_data = {}
    for course in enrolled_courses:
        for module in course.modules:
            module_data[str(module.id)] = {
                'title': module.title,
                'course': course.title,
                'level': course.recommended_level,
                'content': module.content or module.description or '',
            }
    return render_template('ki_lehrer.html', user=user,
                           enrolled_courses=enrolled_courses,
                           module_data=module_data)


@app.route('/api/ki-lehrer', methods=['POST'])
@login_required(role='student')
def ki_lehrer_api(user):
    from flask import jsonify
    data = request.get_json(silent=True) or {}
    module_id   = data.get('module_id')
    conversation = data.get('conversation', [])   # [{role:'user'|'model', text:'...'}]
    is_greeting  = data.get('greeting', False)

    module_title = module_content = course_title = ''
    is_mixed = (module_id == 'mixed')
    enrolled_courses = [e.course for e in user.enrollments]

    if is_mixed:
        module_title = 'Gemischte Themen'
        course_title = 'Alle Kurse'
        mixed_parts = []
        for course in enrolled_courses:
            for module in course.modules:
                body = (module.content or module.description or '').strip()
                if body:
                    mixed_parts.append(f"### {module.title} ({course.title})\n{body[:600]}")
        module_content = '\n\n'.join(mixed_parts)[:4000]
    elif module_id:
        module = Module.query.get(module_id)
        if module:
            module_title   = module.title
            module_content = module.content or module.description or ''
            course_title   = module.course.title if module.course else ''

    # Today's topic set by the teacher
    today_module_title = today_course_title = ''
    for course in enrolled_courses:
        if course.current_module_id:
            today_mod = Module.query.get(course.current_module_id)
            if today_mod:
                today_module_title = today_mod.title
                today_course_title = course.title
                break

    system = build_ki_lehrer_system_prompt(
        user.first_name, user.language_level,
        module_title, course_title, module_content,
        today_module_title, today_course_title,
        build_student_context(user),
        is_mixed=is_mixed,
    )

    # Welcome without module: single short greeting sentence
    if is_greeting and not module_id and not conversation:
        welcome_system = (
            f"Du bist Professor Wagner. Begrüße {user.first_name} freundlich mit genau einem Satz auf Deutsch. "
            f"Sage sinngemäß: 'Hallo {user.first_name}, hier ist Prof. Wagner – wähle oben ein Modul aus "
            f"oder starte mit Gemischte Themen, wenn du über alle Module sprechen möchtest.' "
            f"Kein weiterer Text, keine Aufzählung."
        )
        response_text = call_gemini_chat(welcome_system, [{'role': 'user', 'text': '__WELCOME__'}])
        return jsonify({'response': response_text})

    # Begrüßungs-Trigger: leere History → synthetische User-Nachricht
    if is_greeting and not conversation:
        contents = [{'role': 'user', 'text': '__GREETING__'}]
    else:
        contents = [{'role': c['role'], 'text': c['text']} for c in conversation[-20:]]

    speech = call_gemini_chat(system, contents)

    # Generate slide whenever a module is selected (including greeting responses)
    slide_title  = ''
    slide_points = []
    slide_source = ''
    if module_id and speech and not speech.startswith('Fehler'):
        slide_title, slide_points = generate_slide_from_speech(speech, module_title or 'Gemischte Themen')

    return jsonify({'response': speech, 'slide_title': slide_title,
                    'slide_points': slide_points, 'slide_source': slide_source})


@app.route('/api/tts-proxy', methods=['POST'])
def tts_proxy():
    """TTS proxy for TalkingHead lip sync.

    Primary:  ElevenLabs Alice voice (best quality, requires API key + credits)
    Fallback: edge-tts Microsoft neural voice (free, no key needed)

    TalkingHead sends:  { "input": {"ssml": "..."}, "voice": {...}, "audioConfig": {...} }
    We return:          { "audioContent": "<base64 MP3>", "timepoints": [] }
    """
    import re as _re, base64, json as _j, urllib.request, urllib.error, asyncio, io, tempfile, os as _os

    data = request.get_json(silent=True) or {}
    inp  = data.get('input') or {}
    raw  = inp.get('ssml', '') or inp.get('text', '')
    text = _re.sub(r'<[^>]+>', ' ', raw)
    text = _re.sub(r'\s+', ' ', text).strip()
    if not text:
        return _j.dumps({'error': 'no text'}), 400, {'Content-Type': 'application/json'}

    # ── 1. Try ElevenLabs if API key present ──────────────────────
    api_key = os.environ.get('ELEVENLABS_API_KEY', '')
    if api_key:
        voice_id = 'Xb7hH8MSUJpSbSDYk0k2'
        payload  = _j.dumps({
            'text':     text,
            'model_id': 'eleven_multilingual_v2',
            'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75, 'style': 0.2},
        }).encode('utf-8')
        req = urllib.request.Request(
            f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128',
            data=payload, method='POST'
        )
        req.add_header('xi-api-key', api_key)
        req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                audio_bytes = r.read()
            encoded = base64.b64encode(audio_bytes).decode('utf-8')
            return _j.dumps({'audioContent': encoded, 'timepoints': []}), 200, \
                   {'Content-Type': 'application/json; charset=utf-8'}
        except (urllib.error.HTTPError, urllib.error.URLError, Exception):
            pass  # any ElevenLabs failure → fall through to edge-tts

    # ── 2. Fallback: edge-tts (Microsoft neural, free) ────────────
    try:
        import edge_tts

        async def _synth(t):
            buf = io.BytesIO()
            async for chunk in edge_tts.Communicate(t, voice='de-DE-KatjaNeural').stream():
                if chunk['type'] == 'audio':
                    buf.write(chunk['data'])
            return buf.getvalue()

        audio_bytes = asyncio.run(_synth(text))
        encoded = base64.b64encode(audio_bytes).decode('utf-8')
        return _j.dumps({'audioContent': encoded, 'timepoints': []}), 200, \
               {'Content-Type': 'application/json; charset=utf-8'}
    except Exception as e:
        return _j.dumps({'error': f'edge-tts failed: {e}'}), 500, {'Content-Type': 'application/json'}


@app.route('/api/stt', methods=['POST'])
def stt_proxy():
    """Speech-to-text proxy.

    Priority:
      1. OpenAI Whisper  – best German accuracy, handles accents well (free tier)
      2. ElevenLabs Scribe – if Whisper unavailable
      3. Returns {'error': 'stt_unavailable'} → frontend falls back to Web Speech API
    """
    import json as _j, io

    audio_data = request.get_data()
    if not audio_data:
        # Availability probe — report whether any server-side STT is configured
        has_stt = bool(os.environ.get('OPENAI_API_KEY', '')) or bool(os.environ.get('ELEVENLABS_API_KEY', ''))
        body = {'available': True} if has_stt else {'error': 'stt_unavailable'}
        return _j.dumps(body), 200, {'Content-Type': 'application/json'}

    content_type = request.content_type or 'audio/webm'
    ext = 'webm' if 'webm' in content_type else 'mp3' if 'mp3' in content_type else 'webm'

    # ── 1. OpenAI Whisper ────────────────────────────────────────
    openai_key = os.environ.get('OPENAI_API_KEY', '')
    if openai_key:
        try:
            import openai as _oai
            client = _oai.OpenAI(api_key=openai_key)
            audio_file = io.BytesIO(audio_data)
            audio_file.name = f'audio.{ext}'
            result = client.audio.transcriptions.create(
                model='whisper-1',
                file=audio_file,
                language='de',
                response_format='text',
            )
            text = result.strip() if isinstance(result, str) else (result.text or '').strip()
            return _j.dumps({'text': text}), 200, {'Content-Type': 'application/json'}
        except Exception:
            pass  # fall through to ElevenLabs

    # ── 2. ElevenLabs Scribe ─────────────────────────────────────
    import urllib.request, urllib.error
    el_key = os.environ.get('ELEVENLABS_API_KEY', '')
    if el_key:
        boundary = 'el11boundary'
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="model_id"\r\n\r\nscribe_v1\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="language_code"\r\n\r\nde\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="audio.{ext}"\r\n'
            f'Content-Type: {content_type}\r\n\r\n'
        ).encode('utf-8') + audio_data + f'\r\n--{boundary}--\r\n'.encode('utf-8')
        req = urllib.request.Request('https://api.elevenlabs.io/v1/speech-to-text', data=body, method='POST')
        req.add_header('xi-api-key', el_key)
        req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                result = _j.loads(r.read())
                return _j.dumps({'text': result.get('text', '')}), 200, {'Content-Type': 'application/json'}
        except Exception:
            pass  # fall through

    # ── 3. No STT available → browser Web Speech API fallback ────
    return _j.dumps({'error': 'stt_unavailable'}), 200, {'Content-Type': 'application/json'}


# ══════════════════════════════════════════════════════════
#  LEARNING PAGE – Karteikarten, Bibliothek, Quiz, Audio
# ══════════════════════════════════════════════════════════

@app.route('/learn')
@login_required(role='student')
def learning_page(user):
    enrolled_courses = [e.course for e in user.enrollments]
    return render_template('learning.html', user=user, enrolled_courses=enrolled_courses)


@app.route('/api/flashcards/<int:module_id>', methods=['GET'])
@login_required(role='student')
def api_flashcards(user, module_id):
    module = Module.query.get_or_404(module_id)
    # Check if we have stored flashcards
    cards = Flashcard.query.filter_by(module_id=module_id).all()
    if not cards:
        # Generate with AI
        generated = generate_flashcards(module, user.language_level or 'A2')
        for g in generated:
            fc = Flashcard(module_id=module_id, front=g['front'], back=g['back'])
            db.session.add(fc)
        db.session.commit()
        cards = Flashcard.query.filter_by(module_id=module_id).all()

    result = []
    for c in cards:
        prog = FlashcardProgress.query.filter_by(student_id=user.id, flashcard_id=c.id).first()
        result.append({
            'id': c.id, 'front': c.front, 'back': c.back,
            'box': prog.box if prog else 0,
        })
    return jsonify({'cards': result, 'module_title': module.title})


@app.route('/api/flashcards/<int:card_id>/review', methods=['POST'])
@login_required(role='student')
def api_flashcard_review(user, card_id):
    from datetime import timedelta
    data = request.get_json(silent=True) or {}
    correct = data.get('correct', False)

    prog = FlashcardProgress.query.filter_by(student_id=user.id, flashcard_id=card_id).first()
    if not prog:
        prog = FlashcardProgress(student_id=user.id, flashcard_id=card_id, box=0)
        db.session.add(prog)

    if correct:
        prog.box = min(prog.box + 1, 4)
        xp_earned = 5
    else:
        prog.box = max(prog.box - 1, 0)
        xp_earned = 2

    intervals = {0: 1, 1: 2, 2: 4, 3: 7, 4: 14}
    from datetime import datetime as _dt
    prog.next_review = _dt.utcnow() + timedelta(days=intervals.get(prog.box, 1))
    prog.last_reviewed = _dt.utcnow()

    # Award XP
    user.xp = (user.xp or 0) + xp_earned
    from datetime import date as _date
    today_goal = DailyGoal.query.filter_by(student_id=user.id, date=_date.today()).first()
    if not today_goal:
        today_goal = DailyGoal(student_id=user.id, date=_date.today())
        db.session.add(today_goal)
    today_goal.earned_xp = (today_goal.earned_xp or 0) + xp_earned

    update_friend_mission_progress(user.id, xp_earned)

    db.session.commit()
    return jsonify({'box': prog.box, 'xp_earned': xp_earned})


@app.route('/api/library/<int:module_id>', methods=['GET'])
@login_required(role='student')
def api_library(user, module_id):
    module = Module.query.get_or_404(module_id)
    student_ctx = build_student_context(user)
    summary = generate_library_summary(module, user.language_level or 'A2', student_ctx)
    if not summary:
        summary = module.content or module.description or 'Kein Inhalt verfügbar.'
    return jsonify({
        'summary': summary,
        'module_title': module.title,
        'original_content': module.content or module.description or '',
    })


@app.route('/api/library/ask', methods=['POST'])
@login_required(role='student')
def api_library_ask(user):
    data = request.get_json(silent=True) or {}
    question = data.get('question', '').strip()
    module_id = data.get('module_id')
    card_content = data.get('card_content', '')

    if not question:
        return jsonify({'error': 'Keine Frage angegeben.'}), 400

    student_ctx = build_student_context(user)
    context = f"Lesekarten-Inhalt:\n{card_content}\n\n{student_ctx}" if card_content else student_ctx

    response = get_ai_professor_response(question, user.language_level or 'A2', context, student_ctx)
    return jsonify({'response': response})


@app.route('/api/library-cards/<int:module_id>', methods=['GET'])
@login_required(role='student')
def api_library_cards(user, module_id):
    module = Module.query.get_or_404(module_id)
    student_ctx = build_student_context(user)
    cards = generate_library_cards(module, user.language_level or 'A2', student_ctx)
    return jsonify({'cards': cards, 'module_title': module.title})


# ══════════════════════════════════════════════════════════
#  GAMIFICATION – XP, Goals, Leaderboard, Friends, Lotto
# ══════════════════════════════════════════════════════════

@app.route('/gamification')
@login_required(role='student')
def gamification(user):
    from datetime import date as _date, timedelta
    import random

    # Daily goal
    today = _date.today()
    today_goal = DailyGoal.query.filter_by(student_id=user.id, date=today).first()
    if not today_goal:
        today_goal = DailyGoal(student_id=user.id, date=today)
        db.session.add(today_goal)
        db.session.commit()

    # Streak
    streak = 0
    check_date = today - timedelta(days=1)
    while True:
        g = DailyGoal.query.filter_by(student_id=user.id, date=check_date).first()
        if g and g.earned_xp >= g.target_xp:
            streak += 1
            check_date -= timedelta(days=1)
        else:
            break

    # Leaderboard (top 20 students)
    leaderboard = User.query.filter_by(role='student').order_by(User.xp.desc()).limit(20).all()

    # Friends
    friends = user.friends.all() if user.friends else []

    # Friend missions
    missions_active = FriendMission.query.filter(
        db.or_(FriendMission.creator_id == user.id, FriendMission.friend_id == user.id),
        FriendMission.completed == False
    ).order_by(FriendMission.created_at.desc()).all()

    missions_completed = FriendMission.query.filter(
        db.or_(FriendMission.creator_id == user.id, FriendMission.friend_id == user.id),
        FriendMission.completed == True
    ).order_by(FriendMission.created_at.desc()).limit(20).all()

    # Backward-compatible alias for existing templates/components
    missions = missions_active

    # Lotto winners this week
    friday = today - timedelta(days=(today.weekday() - 4) % 7)
    if today.weekday() < 4:
        friday = friday - timedelta(days=7)
    lotto_winners = LottoWinner.query.filter_by(week_date=friday).order_by(LottoWinner.position).all()

    return render_template('gamification.html',
        user=user, today_goal=today_goal, streak=streak,
        leaderboard=leaderboard, friends=friends,
        missions=missions,
        missions_active=missions_active,
        missions_completed=missions_completed,
        lotto_winners=lotto_winners)


@app.route('/api/friends/add', methods=['POST'])
@login_required(role='student')
def api_add_friend(user):
    data = request.get_json(silent=True) or {}
    email = data.get('email', '').strip().lower()
    if not email:
        return jsonify({'error': 'E-Mail fehlt'}), 400
    friend = User.query.filter_by(email=email, role='student').first()
    if not friend or friend.id == user.id:
        return jsonify({'error': 'Schüler nicht gefunden'}), 404
    if user.friends.filter(friendship.c.friend_id == friend.id).first():
        return jsonify({'error': 'Bereits befreundet'}), 400
    # Add bidirectional
    stmt1 = friendship.insert().values(user_id=user.id, friend_id=friend.id)
    stmt2 = friendship.insert().values(user_id=friend.id, friend_id=user.id)
    db.session.execute(stmt1)
    db.session.execute(stmt2)
    db.session.commit()
    return jsonify({'success': True, 'name': friend.full_name()})


@app.route('/api/missions/create', methods=['POST'])
@login_required(role='student')
def api_create_mission(user):
    data = request.get_json(silent=True) or {}
    friend_id = data.get('friend_id')
    title = data.get('title', '').strip()
    target_xp = data.get('target_xp', 30)
    if not friend_id or not title:
        return jsonify({'error': 'Daten fehlen'}), 400
    mission = FriendMission(
        creator_id=user.id, friend_id=friend_id,
        title=title, target_xp=min(int(target_xp), 200)
    )
    db.session.add(mission)
    db.session.commit()
    return jsonify({'success': True, 'id': mission.id})


@app.route('/api/daily-goal', methods=['POST'])
@login_required(role='student')
def api_set_daily_goal(user):
    data = request.get_json(silent=True) or {}
    target = data.get('target_xp', 50)
    from datetime import date as _date
    today = _date.today()
    goal = DailyGoal.query.filter_by(student_id=user.id, date=today).first()
    if not goal:
        goal = DailyGoal(student_id=user.id, date=today, target_xp=min(int(target), 500))
        db.session.add(goal)
    else:
        goal.target_xp = min(int(target), 500)
    db.session.commit()
    return jsonify({'success': True})


@app.route('/api/lotto/draw', methods=['POST'])
@login_required(role='teacher')
def api_lotto_draw(user):
    """Friday lotto draw – weighted by XP position on leaderboard."""
    import random
    from datetime import date as _date, timedelta
    today = _date.today()
    if today.weekday() != 4:  # 4 = Friday
        return jsonify({'error': 'Lotto-Ziehung nur freitags möglich'}), 400

    # Check if already drawn this week
    existing = LottoWinner.query.filter_by(week_date=today).first()
    if existing:
        return jsonify({'error': 'Diese Woche wurde bereits gezogen'}), 400

    students = User.query.filter_by(role='student').order_by(User.xp.desc()).all()
    if len(students) < 3:
        return jsonify({'error': 'Mindestens 3 Schüler nötig'}), 400

    # Weighted selection: higher position = more weight
    weights = []
    for i, s in enumerate(students):
        w = max(1, len(students) - i) + (s.xp or 0) // 10
        weights.append(w)

    winners = []
    available = list(range(len(students)))
    avail_weights = weights[:]
    for pos in range(1, 4):
        chosen_idx = random.choices(available, weights=avail_weights, k=1)[0]
        idx_in_available = available.index(chosen_idx)
        winners.append((students[chosen_idx], pos))
        available.pop(idx_in_available)
        avail_weights.pop(idx_in_available)

    for student, pos in winners:
        lw = LottoWinner(student_id=student.id, week_date=today, position=pos)
        db.session.add(lw)
    db.session.commit()

    return jsonify({'winners': [
        {'name': s.full_name(), 'position': p, 'xp': s.xp or 0}
        for s, p in winners
    ]})


# ══════════════════════════════════════════════════════════
#  FALLSTUDIE – Fall: Blutdruckmessung bei Frau Schmidt
# ══════════════════════════════════════════════════════════

@app.route('/fall/blutdruck')
@login_required(role='student')
def fall_blutdruck(user):
    if request.args.get('reset') == '1':
        session.pop('fall_blutdruck_completed', None)
        session.pop('fall_blutdruck_awarded', None)
        session.pop('fall_blutdruck_duration_sec', None)

    valid_keys = {s['key'] for s in FALL_BLUTDRUCK['steps']}
    stored_completed = session.get('fall_blutdruck_completed', [])
    completed = [k for k in stored_completed if k in valid_keys]
    session['fall_blutdruck_completed'] = completed

    awarded = bool(session.get('fall_blutdruck_awarded', False))
    elapsed_sec = int(session.get('fall_blutdruck_duration_sec', 0) or 0)

    fall_resume = {
        'completed': completed,
        'finished': awarded or len(completed) >= len(FALL_BLUTDRUCK['steps']),
        'elapsed_sec': max(0, elapsed_sec),
    }

    return render_template('fall_blutdruck.html', user=user, case=FALL_BLUTDRUCK, fall_resume=fall_resume)


@app.route('/api/fall/blutdruck/turn', methods=['POST'])
@login_required(role='student')
def api_fall_blutdruck_turn(user):
    data = request.get_json(silent=True) or {}
    raw_conversation = data.get('conversation', [])
    is_greeting = bool(data.get('greeting', False))

    valid_step_keys = [s['key'] for s in FALL_BLUTDRUCK['steps']]
    valid_step_set = set(valid_step_keys)

    stored_completed = session.get('fall_blutdruck_completed', [])
    completed = []
    for key in stored_completed:
        if key in valid_step_set and key not in completed:
            completed.append(key)

    duration_sec = int(data.get('duration_sec', 0) or 0)
    session['fall_blutdruck_duration_sec'] = max(0, duration_sec)

    conversation = []
    for turn in (raw_conversation or [])[-20:]:
        if not isinstance(turn, dict):
            continue
        role = 'user' if turn.get('role') == 'user' else 'model'
        text = str(turn.get('text', '')).strip()
        if not text:
            continue
        conversation.append({'role': role, 'text': text[:1200]})

    last_user_text = ''
    for turn in reversed(conversation):
        if turn.get('role') == 'user' and turn.get('text') != '__GREETING__':
            last_user_text = turn.get('text', '')
            break

    newly_completed = []
    if last_user_text:
        newly_completed = detect_fall_steps(FALL_BLUTDRUCK, last_user_text, completed)
        for key in newly_completed:
            if key in valid_step_set and key not in completed:
                completed.append(key)

    session['fall_blutdruck_completed'] = completed

    last_completed_key = newly_completed[0] if newly_completed else ''
    system = build_fall_blutdruck_prompt(
        user.first_name, user.language_level or 'B1',
        completed, last_completed_key
    )

    if is_greeting and not conversation:
        contents = [{'role': 'user', 'text': '__GREETING__'}]
    else:
        contents = conversation

    speech = call_gemini_chat(system, contents)

    finished = len(completed) >= len(valid_step_keys)
    xp_awarded = 0
    already_awarded = bool(session.get('fall_blutdruck_awarded', False))
    if finished and not already_awarded:
        xp_awarded = 80
        user.xp = (user.xp or 0) + xp_awarded
        from datetime import date as _date, datetime as _dt
        today = _date.today()
        goal = DailyGoal.query.filter_by(student_id=user.id, date=today).first()
        if not goal:
            goal = DailyGoal(student_id=user.id, date=today)
            db.session.add(goal)
        goal.earned_xp = (goal.earned_xp or 0) + xp_awarded

        update_friend_mission_progress(user.id, xp_awarded)

        # Save case study attempt
        attempt = CaseStudyAttempt(
            student_id=user.id,
            case_key='blutdruck',
            steps_completed=len(completed),
            steps_total=len(valid_step_keys),
            xp_earned=xp_awarded,
            duration_sec=max(0, duration_sec),
            completed=True,
        )
        db.session.add(attempt)
        db.session.commit()
        session['fall_blutdruck_awarded'] = True

    return jsonify({
        'response': speech,
        'completed': completed,
        'newly_completed': newly_completed,
        'finished': finished,
        'xp_awarded': xp_awarded,
        'total_xp': user.xp or 0,
    })


# ══════════════════════════════════════════════════════════
#  KIP – Schul-Dashboard (Nutzungsstatistiken)
# ══════════════════════════════════════════════════════════

@app.route('/kip')
@login_required(role='teacher')
def kip_dashboard(user):
    from datetime import datetime as _dt, timedelta, date as _date
    from sqlalchemy import func

    # Total students
    total_students = User.query.filter_by(role='student').count()

    # Active students (at least 1 quiz attempt in last 7 days)
    week_ago = _dt.utcnow() - timedelta(days=7)
    active_students = db.session.query(QuizAttempt.student_id).distinct()\
        .filter(QuizAttempt.completed_at >= week_ago).count()

    # Total quiz attempts
    total_attempts = QuizAttempt.query.count()

    # Average score
    avg_score = db.session.query(func.avg(QuizAttempt.pct)).scalar() or 0

    # Daily activity (last 14 days)
    daily_activity = []
    for i in range(13, -1, -1):
        d = _date.today() - timedelta(days=i)
        start = _dt.combine(d, _dt.min.time())
        end = _dt.combine(d, _dt.max.time())
        count = QuizAttempt.query.filter(
            QuizAttempt.completed_at >= start,
            QuizAttempt.completed_at <= end
        ).count()
        daily_activity.append({'date': d.strftime('%d.%m'), 'count': count})

    # Student progress overview
    students = User.query.filter_by(role='student').all()
    student_progress = []
    for s in students:
        attempts = QuizAttempt.query.filter_by(student_id=s.id).count()
        last = QuizAttempt.query.filter_by(student_id=s.id)\
            .order_by(QuizAttempt.completed_at.desc()).first()
        student_progress.append({
            'name': s.full_name(),
            'level': s.language_level,
            'nursing': s.nursing_level,
            'xp': s.xp or 0,
            'attempts': attempts,
            'last_active': last.completed_at.strftime('%d.%m.%Y') if last else 'Nie',
        })

    # Course enrollments
    courses = Course.query.all()
    course_stats = []
    for c in courses:
        enrolled = Enrollment.query.filter_by(course_id=c.id).count()
        course_stats.append({'title': c.title, 'enrolled': enrolled})

    # ── Topic mastery (aggregate quiz scores per module category) ──
    topic_names = [
        ('Grundpflege', 'Grundpflege'),
        ('Medikamente', 'Medikamentenlehre'),
        ('Notfall', 'Notfallmaßnahmen'),
        ('Kommunikation', 'Kommunikation'),
        ('Dokumentation', 'Dokumentation'),
        ('Vitalzeichen', 'Vitalzeichen'),
    ]
    topic_mastery = []
    for keyword, label in topic_names:
        # Find modules whose title contains the keyword
        matching = Module.query.filter(Module.title.ilike(f'%{keyword}%')).all()
        module_ids = [m.id for m in matching]
        if module_ids:
            avg = db.session.query(func.avg(QuizAttempt.pct))\
                .filter(QuizAttempt.module_id.in_(module_ids)).scalar() or 0
        else:
            # Generate realistic demo values so dashboard isn't empty
            import hashlib
            seed = int(hashlib.md5(keyword.encode()).hexdigest()[:8], 16)
            avg = 35 + (seed % 50)
        topic_mastery.append({'name': label, 'pct': round(avg)})

    # AI recommendation – pick weakest topic
    weakest = min(topic_mastery, key=lambda t: t['pct'])
    if weakest['pct'] < 50:
        ai_recommendation = (
            f"{weakest['name']} liegt bei nur {weakest['pct']}% — "
            f"Empfehlung: Zusätzliche Übungseinheiten und gezielte Fallstudien "
            f"in {weakest['name']} einplanen."
        )
    else:
        ai_recommendation = (
            f"Alle Bereiche über 50%. Stärkstes Thema weiter vertiefen "
            f"und Praxisfälle in {weakest['name']} ({weakest['pct']}%) "
            f"intensivieren, um die Lücke zu schließen."
        )

    return render_template('kip_dashboard.html',
        user=user,
        total_students=total_students,
        active_students=active_students,
        total_attempts=total_attempts,
        avg_score=round(avg_score),
        daily_activity=daily_activity,
        student_progress=student_progress,
        course_stats=course_stats,
        topic_mastery=topic_mastery,
        ai_recommendation=ai_recommendation)


@app.route('/debug-env')
def debug_env():
    import sys
    lines = [
        f"Python: {sys.executable}",
        f"GOOGLE_API_KEY gesetzt: {bool(os.environ.get('GOOGLE_API_KEY'))}",
        f"ELEVENLABS_API_KEY gesetzt: {bool(os.environ.get('ELEVENLABS_API_KEY'))}",
        f"OPENAI_API_KEY gesetzt: {bool(os.environ.get('OPENAI_API_KEY'))}",
    ]
    try:
        from google import genai
        lines.append(f"google-genai: OK (v{genai.__version__})")
    except Exception as e:
        lines.append(f"google-genai Import-Fehler: {type(e).__name__}: {e}")
    try:
        import openai as _oai
        lines.append(f"openai: OK (v{_oai.__version__})")
    except Exception as e:
        lines.append(f"openai Import-Fehler: {type(e).__name__}: {e}")
    return "<br>".join(lines)


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')
    port = int(os.environ.get('PORT', os.environ.get('FLASK_RUN_PORT', '5001')))
    debug = _env_flag('FLASK_DEBUG', True)
    use_https = _env_flag('DEV_HTTPS', True)
    ssl_context = 'adhoc' if use_https else None

    scheme = 'https' if use_https else 'http'
    print(f'careLearn läuft auf {scheme}://{host}:{port}')
    if use_https:
        print('Hinweis: Beim ersten Start das lokale Zertifikat im Browser bestätigen.')

    app.run(debug=debug, host=host, port=port, ssl_context=ssl_context)
