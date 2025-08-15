import os
import traceback
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from openai import OpenAI
from dotenv import load_dotenv
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, text, desc
from datetime import datetime, timedelta
from flask_jwt_extended import create_access_token, jwt_required, JWTManager
from werkzeug.security import check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_talisman import Talisman
from serpapi import GoogleSearch # Ajout de la librairie pour SerpApi

# --- CONFIGURATION INITIALE ---
load_dotenv()
app = Flask(__name__)
CORS(app, supports_credentials=True)

# --- CONFIGURATION DE LA SÉCURITÉ ---
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "une-super-cle-secrete-pour-le-developpement-gallopin")
DASHBOARD_PASSWORD = "GallopinDashboard2025!"
jwt = JWTManager(app)
talisman = Talisman(app, content_security_policy=None)
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# --- CLIENTS API ---
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
SERPAPI_API_KEY = os.getenv("SERPAPI_API_KEY")

# --- CONFIGURATION DE LA BASE DE DONNÉES ---
database_url = "postgresql://gallopin_db_user:c3aa63Gd8HOfNrrF2tZlKeG7GCHgfFps@dpg-d2fjn5emcj7s73euk0b0-a/gallopin_db"
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- MODÈLES DE LA BASE DE DONNÉES ---
class GeneratedReview(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(80), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)

class Server(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class FlavorOption(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(100), nullable=False)
    category = db.Column(db.String(50), nullable=False)

class MenuSelection(db.Model):
    __tablename__ = 'menu_selections'
    id = db.Column(db.Integer, primary_key=True)
    dish_name = db.Column(db.Text, nullable=False)
    dish_category = db.Column(db.Text, nullable=False)
    selection_timestamp = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)

class InternalFeedback(db.Model):
    __tablename__ = 'internal_feedback'
    id = db.Column(db.Integer, primary_key=True)
    feedback_text = db.Column(db.Text, nullable=False)
    associated_server_id = db.Column(db.Integer, db.ForeignKey('server.id', ondelete='SET NULL'), nullable=True, index=True)
    status = db.Column(db.Text, nullable=False, default='new', index=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now(), index=True)
    server = db.relationship('Server')

class QualitativeFeedback(db.Model):
    __tablename__ = 'qualitative_feedback'
    id = db.Column(db.Integer, primary_key=True)
    category = db.Column(db.String(100), nullable=False, index=True)
    value = db.Column(db.String(100), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

# --- INITIALISATION ET PEUPLEMENT DE LA BASE DE DONNÉES ---
def seed_database():
    if FlavorOption.query.first() is not None: return
    menu_gallopin = {
        "Entrées": ["Burrata aubergine / Burrata di Parme", "Œuf cassé aux girolles", "Petits violets à cru au parmesan", "Les six Gros escargots de Bourgogne", "Œufs bio mayo", "Moules gratinées", "Nems au poulet", "Carpaccio de Daurade", "Avocat thon épicé", "Calamars creamy spicy", "Cœur de Saumon blinis", "Pizza Truffes"],
        "Plats": ["Risotto aux cêpes", "Paccheri aux morilles", "Gratin de ravioles", "Le foie de veau du GALLOPIN", "Cabillaud creamy spicy", "Saumon miso", "Daurade Royale au four", "La sole", "Agneau en petites côtelettes", "Tartare Brasserie", "Le Poivre dans le filet ou Béarnaise", "Coquelet Roti", "Classique escalope de veau"],
        "Desserts": ["Saint Marcellin", "Pruneaux à l'Armagnac", "Omelette Norvégienne", "Tarte fine aux pommes", "Fraises & Framboises chantilly", "Pavlova aux fruits rouges", "L'énorme crème caramel", "Ile flottante", "Tartelette citron", "Mousse au chocolat", "La fameuse Brioche retrouvée", "Baba au Rhum", "Profiteroles", "Glaces et sorbets"]
    }
    for category, dishes in menu_gallopin.items():
        for dish_name in dishes:
            db.session.add(FlavorOption(text=dish_name.strip(), category=category))
    db.session.commit()

with app.app_context():
    db.create_all()
    seed_database()

# --- ROUTES API ---
@app.route("/api/login", methods=["POST"])
@limiter.limit("10 per minute")
def login():
    username = request.json.get("username", None)
    password = request.json.get("password", None)
    if username != "admin" or password != DASHBOARD_PASSWORD:
        return jsonify({"msg": "Identifiants incorrects"}), 401
    return jsonify(access_token=create_access_token(identity=username))

@app.route('/api/servers', methods=['GET', 'POST'])
@jwt_required()
def manage_servers():
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('name'): return jsonify({"error": "Nom manquant."}), 400
        new_server = Server(name=data['name'].strip().title())
        db.session.add(new_server)
        db.session.commit()
        return jsonify({"id": new_server.id, "name": new_server.name}), 201
    servers = Server.query.order_by(Server.name).all()
    return jsonify([{"id": s.id, "name": s.name} for s in servers])

@app.route('/api/servers/<int:server_id>', methods=['PUT', 'DELETE'])
@jwt_required()
def handle_server(server_id):
    server = db.session.get(Server, server_id)
    if not server: return jsonify({"error": "Serveur non trouvé."}), 404
    if request.method == 'PUT':
        data = request.get_json()
        if not data or not data.get('name'): return jsonify({"error": "Nom du serveur manquant."}), 400
        server.name = data['name'].strip().title()
        db.session.commit()
        return jsonify({"id": server.id, "name": server.name})
    if request.method == 'DELETE':
        GeneratedReview.query.filter_by(server_name=server.name).delete()
        db.session.delete(server)
        db.session.commit()
        return jsonify({"success": True})

@app.route('/api/options/flavors', methods=['GET', 'POST'])
@jwt_required()
def manage_flavors():
    if request.method == 'POST':
        data = request.get_json()
        if not data or not data.get('text') or not data.get('category'): return jsonify({"error": "Données manquantes."}), 400
        new_option = FlavorOption(text=data['text'].strip(), category=data['category'].strip())
        db.session.add(new_option)
        db.session.commit()
        return jsonify({"id": new_option.id, "text": new_option.text, "category": new_option.category}), 201
    options = FlavorOption.query.all()
    return jsonify([{"id": opt.id, "text": opt.text, "category": opt.category} for opt in options])

@app.route('/api/options/flavors/<int:option_id>', methods=['PUT', 'DELETE'])
@jwt_required()
def handle_flavor(option_id):
    option = db.session.get(FlavorOption, option_id)
    if not option: return jsonify({"error": "Option non trouvée."}), 404
    if request.method == 'PUT':
        data = request.get_json()
        if not data or not data.get('text') or not data.get('category'): return jsonify({"error": "Données de l'option manquantes."}), 400
        option.text = data['text'].strip()
        option.category = data['category'].strip()
        db.session.commit()
        return jsonify({"id": option.id, "text": option.text, "category": option.category})
    if request.method == 'DELETE':
        db.session.delete(option)
        db.session.commit()
        return jsonify({"success": True})

@app.route('/api/public/data', methods=['GET'])
def get_public_data():
    try:
        servers = Server.query.order_by(Server.name).all()
        flavors = FlavorOption.query.all()
        flavors_by_category = {}
        for f in flavors:
            if f.category not in flavors_by_category:
                flavors_by_category[f.category] = []
            flavors_by_category[f.category].append({"id": f.id, "text": f.text})
        data = {
            "servers": [{"id": s.id, "name": s.name} for s in servers],
            "flavors": flavors_by_category,
        }
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": "Impossible de charger les données de configuration."}), 500

@app.route('/generate-review', methods=['POST'])
def generate_review():
    data = request.get_json()
    if not data: return jsonify({"error": "Données invalides."}), 400
    
    lang = data.get('lang', 'fr')
    tags = data.get('tags', [])
    private_feedback = data.get('private_feedback', '').strip()

    has_public_review_data = any(tag.get('category') not in ['server_name', 'reason_for_visit'] for tag in tags) or len(tags) > 1
    has_private_feedback = bool(private_feedback)

    if not has_public_review_data and not has_private_feedback:
        return jsonify({"error": "Aucune donnée à traiter."}), 400

    details = {}
    dish_selections = []
    
    for tag in tags:
        category, value = tag.get('category'), tag.get('value')
        if category in ['service_qualities', 'atmosphere', 'reason_for_visit', 'quick_highlight'] and value:
            db.session.add(QualitativeFeedback(category=category, value=value))
        if category and value:
            if category not in details: details[category] = []
            details[category].append(value)
            if category == 'dish':
                flavor_option = FlavorOption.query.filter_by(text=value).first()
                if flavor_option: dish_selections.append({"name": value, "category": flavor_option.category})

    server_name = details.get('server_name', [None])[0]
    if has_private_feedback:
        server_id = None
        if server_name:
            server_obj = Server.query.filter_by(name=server_name).first()
            if server_obj: server_id = server_obj.id
        db.session.add(InternalFeedback(feedback_text=private_feedback, associated_server_id=server_id))
    if server_name: db.session.add(GeneratedReview(server_name=server_name))
    for dish in dish_selections: db.session.add(MenuSelection(dish_name=dish['name'], dish_category=dish['category']))

    try:
        db.session.commit()
        if not has_public_review_data: return jsonify({"message": "Feedback enregistré."})

        prompt_text = f"Rédige un avis client positif et chaleureux pour la brasserie parisienne Gallopin, en langue '{lang}'. L'avis doit sembler authentique et personnel. Incorpore les éléments suivants de manière naturelle:\n"
        for category, values in details.items():
            if category != 'server_name': prompt_text += f"- {category}: {', '.join(values)}\n"
        if server_name: prompt_text += f"\nL'avis doit mentionner le service impeccable de {server_name}.\n"
        prompt_text += "\nL'avis doit faire environ 4-6 phrases."

        completion = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "Tu es un assistant de rédaction spécialisé dans les avis de restaurants."},
                {"role": "user", "content": prompt_text}
            ],
            temperature=0.7, max_tokens=200
        )
        return jsonify({"review": completion.choices[0].message.content.strip()})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Erreur lors de la génération de l'avis."}), 500

# --- NOUVELLE FONCTIONNALITÉ SIF BASÉE SUR LES AVIS GOOGLE ---

def get_google_reviews():
    """Récupère les avis Google en utilisant SerpApi."""
    if not SERPAPI_API_KEY:
        print("Avertissement : Clé SERPAPI_API_KEY non configurée.")
        return None
    params = {
        "engine": "google_maps_reviews",
        "place_id": "ChIJyW6wknNv5kcRTdWzsViISUg", # Place ID pour Gallopin
        "api_key": SERPAPI_API_KEY,
        "hl": "fr"
    }
    search = GoogleSearch(params)
    results = search.get_dict()
    return results.get("reviews")

def analyze_reviews_with_ai(reviews):
    """Analyse une liste d'avis avec l'API OpenAI."""
    if not reviews: return None
    review_texts = [review.get("snippet", "") for review in reviews[:20] if review.get("snippet")]
    if not review_texts: return None

    prompt = f"""
    Tu es un expert en analyse de restaurants. Analyse les {len(review_texts)} avis suivants pour la brasserie parisienne Gallopin.
    Avis : {json.dumps(review_texts, ensure_ascii=False)}
    Basé uniquement sur ces avis, fournis un résumé en français. Ta réponse doit être un objet JSON unique avec les clés suivantes :
    - "strengths": une liste de 4 points positifs clés mentionnés fréquemment.
    - "weaknesses": une liste de 3 points faibles clés ou axes d'amélioration.
    - "suggestions": une liste de 2 suggestions concrètes basées sur les points faibles.
    - "categories": une liste d'objets avec "name" (Service, Cuisine, Ambiance, Rapport Q/P) et "score" (un entier de 0 à 100).
    """
    try:
        completion = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "Tu es un assistant d'analyse qui répond en format JSON."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.5
        )
        return json.loads(completion.choices[0].message.content)
    except Exception as e:
        print(f"Erreur lors de l'analyse OpenAI : {e}")
        return None

@app.route('/api/sif-synthesis')
@jwt_required()
def sif_synthesis():
    """Route principale pour la SIF."""
    try:
        reviews = get_google_reviews()
        if not reviews: raise Exception("Impossible de récupérer les avis Google. Vérifiez la clé SERPAPI_API_KEY.")
        analysis = analyze_reviews_with_ai(reviews)
        if not analysis: raise Exception("L'analyse par IA a échoué.")
        analysis["sentiment_trend"] = [{"date": (datetime.utcnow() - timedelta(days=i)).isoformat(), "score": 80 - i*2 + (i%3)*4} for i in range(14)][::-1]
        return jsonify(analysis)
    except Exception as e:
        print(f"Erreur dans /api/sif-synthesis: {e}")
        return jsonify({
            "strengths": ["Analyse en cours..."], "weaknesses": ["Données insuffisantes pour le moment."],
            "suggestions": [], "sentiment_trend": [], "categories": []
        }), 500

# --- ROUTES DU DASHBOARD ---
@app.route('/dashboard')
@jwt_required()
def dashboard_data():
    period = request.args.get('period', 'all')
    try:
        base_query = GeneratedReview.query
        end_date = datetime.utcnow()
        if period == '7days': base_query = base_query.filter(GeneratedReview.created_at >= (end_date - timedelta(days=7)))
        elif period == '30days': base_query = base_query.filter(GeneratedReview.created_at >= (end_date - timedelta(days=30)))
        
        reviews_in_period = base_query.count()
        first_review_date = db.session.query(func.min(GeneratedReview.created_at)).scalar()
        days_in_period = (end_date.date() - first_review_date.date()).days if first_review_date else 0
        average_reviews_per_day = round(reviews_in_period / days_in_period, 1) if days_in_period > 0 else float(reviews_in_period)

        trend_data_dict = { (datetime.utcnow().date() - timedelta(days=i)): 0 for i in range(14) }
        fourteen_days_ago = datetime.utcnow().date() - timedelta(days=13)
        trend_results = db.session.query(func.date(GeneratedReview.created_at), func.count(GeneratedReview.id)).filter(func.date(GeneratedReview.created_at) >= fourteen_days_ago).group_by(func.date(GeneratedReview.created_at)).all()
        for date, count in trend_results:
            if date in trend_data_dict: trend_data_dict[date] = count
        
        return jsonify({
            "stats": {"reviews_in_period": reviews_in_period, "average_reviews_per_day": average_reviews_per_day},
            "trend": [{"date": dt.isoformat(), "count": count} for dt, count in sorted(trend_data_dict.items())]
        })
    except Exception as e:
        return jsonify({"error": "Impossible de charger les données."}), 500

@app.route('/api/server-stats')
@jwt_required()
def server_stats():
    period = request.args.get('period', 'all')
    query = db.session.query(GeneratedReview.server_name, func.count(GeneratedReview.id).label('review_count'))
    if period == '7days': query = query.filter(GeneratedReview.created_at >= (datetime.utcnow() - timedelta(days=7)))
    elif period == '30days': query = query.filter(GeneratedReview.created_at >= (datetime.utcnow() - timedelta(days=30)))
    ranking_results = query.group_by(GeneratedReview.server_name).order_by(desc('review_count')).all()
    return jsonify([{"server": server, "count": count} for server, count in ranking_results])

@app.route('/api/menu-performance')
@jwt_required()
def menu_performance_data():
    period = request.args.get('period', 'all')
    query = db.session.query(MenuSelection.dish_name, MenuSelection.dish_category, func.count(MenuSelection.id).label('selection_count'))
    if period == '7days': query = query.filter(MenuSelection.selection_timestamp >= (datetime.utcnow() - timedelta(days=7)))
    elif period == '30days': query = query.filter(MenuSelection.selection_timestamp >= (datetime.utcnow() - timedelta(days=30)))
    results = query.group_by(MenuSelection.dish_name, MenuSelection.dish_category).order_by(desc('selection_count')).all()
    return jsonify([{"dish_name": n, "dish_category": c, "selection_count": s} for n, c, s in results])

@app.route('/api/internal-feedback', methods=['GET'])
@jwt_required()
def get_internal_feedback():
    status_filter = request.args.get('status', 'all')
    query = db.session.query(InternalFeedback, Server.name).outerjoin(Server, InternalFeedback.associated_server_id == Server.id)
    if status_filter != 'all': query = query.filter(InternalFeedback.status == status_filter)
    results = query.order_by(desc(InternalFeedback.created_at)).all()
    return jsonify([{"id": fb.id, "feedback_text": fb.feedback_text, "status": fb.status, "created_at": fb.created_at.isoformat(), "server_name": s_name if s_name else "Non spécifié"} for fb, s_name in results])

@app.route('/api/internal-feedback/<int:feedback_id>/status', methods=['PUT'])
@jwt_required()
def update_feedback_status(feedback_id):
    data = request.get_json()
    new_status = data.get('status')
    if not new_status or new_status not in ['read', 'archived', 'new']: return jsonify({"error": "Statut invalide."}), 400
    feedback = db.session.get(InternalFeedback, feedback_id)
    if not feedback: return jsonify({"error": "Feedback non trouvé."}), 404
    feedback.status = new_status
    db.session.commit()
    return jsonify({"success": True})

@app.route('/api/reset-data', methods=['POST'])
@jwt_required()
def reset_data():
    try:
        db.session.execute(text('TRUNCATE TABLE generated_review, menu_selections, internal_feedback, qualitative_feedback RESTART IDENTITY CASCADE;'))
        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        db.session.rollback()
        return jsonify({"error": "Erreur lors de la réinitialisation."}), 500

if __name__ == '__main__':
    app.run(debug=True)
