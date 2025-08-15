import os
from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from dotenv import load_dotenv
from openai import OpenAI

# Charger les variables d'environnement depuis le fichier .env
load_dotenv()

app = Flask(__name__)
CORS(app)

# --- Configuration de la base de données ---
# Utilise la variable d'environnement DATABASE_URL fournie par Render, sinon une base de données SQLite locale.
db_url = os.environ.get("DATABASE_URL")
if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = db_url or 'sqlite:///gallopin.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- Configuration de l'API OpenAI ---
# Assurez-vous que votre clé API est dans le fichier .env ou dans les variables d'environnement de Render
try:
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
except Exception as e:
    print(f"ERREUR: Impossible d'initialiser le client OpenAI. Assurez-vous que la variable d'environnement OPENAI_API_KEY est définie. Erreur: {e}")
    client = None

# --- Modèles de base de données ---

class FlavorOption(db.Model):
    """ Modèle pour les plats du menu. """
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String(120), nullable=False)
    category = db.Column(db.String(80), nullable=False)

    def to_dict(self):
        return {"id": self.id, "text": self.text, "category": self.category}

class Feedback(db.Model):
    """ Modèle pour le feedback interne. """
    id = db.Column(db.Integer, primary_key=True)
    server_name = db.Column(db.String(100), nullable=False)
    feedback_text = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

class ReviewSelection(db.Model):
    """ Modèle pour suivre les plats sélectionnés dans les avis. """
    id = db.Column(db.Integer, primary_key=True)
    flavor_id = db.Column(db.Integer, db.ForeignKey('flavor_option.id'), nullable=False)
    flavor = db.relationship('FlavorOption')
    count = db.Column(db.Integer, default=1)

# --- Routes de l'API ---

@app.route('/')
def get_flavor_options():
    """ Retourne tous les plats groupés par catégorie. """
    try:
        options = FlavorOption.query.all()
        categorized_options = {}
        for option in options:
            if option.category not in categorized_options:
                categorized_options[option.category] = []
            categorized_options[option.category].append(option.to_dict())
        return jsonify(categorized_options)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/generate-review', methods=['POST'])
def generate_review_route():
    """ Génère un avis client en utilisant l'IA d'OpenAI. """
    if not client:
        return jsonify({"error": "Le client OpenAI n'est pas configuré."}), 500

    data = request.json
    selected_flavors = data.get('flavors', [])
    selected_tags = data.get('tags', [])
    rating = data.get('rating')
    custom_notes = data.get('customNotes', '') # On garde cette variable même si le front ne l'envoie pas

    # Mettre à jour le compteur pour chaque plat sélectionné
    for flavor_text in selected_flavors:
        flavor_option = FlavorOption.query.filter_by(text=flavor_text).first()
        if flavor_option:
            selection = ReviewSelection.query.filter_by(flavor_id=flavor_option.id).first()
            if selection:
                selection.count += 1
            else:
                selection = ReviewSelection(flavor_id=flavor_option.id)
                db.session.add(selection)
    db.session.commit()

    # Construction du prompt pour ChatGPT
    prompt_text = f"""
    Rédige un avis client authentique et élégant pour Gallopin, une brasserie parisienne historique et raffinée fondée en 1876.
    Le ton doit être celui d'un client satisfait qui partage une expérience mémorable.
    L'avis doit être rédigé en français.

    Voici les détails de l'expérience :
    - Note attribuée : {rating}/5 étoiles.
    - Plats dégustés : {', '.join(selected_flavors)}.
    - Aspects particulièrement appréciés (tags) : {', '.join(selected_tags)}.
    - Notes additionnelles du client : "{custom_notes}"

    Instructions de rédaction :
    1. Commence par une phrase d'accroche captivante qui reflète le cadre unique de Gallopin.
    2. Intègre naturellement les plats et les tags dans le corps du texte.
    3. Si des notes additionnelles sont fournies, inspire-t'en pour ajouter une touche personnelle.
    4. Conclus sur une note positive, en recommandant l'établissement.
    5. La réponse doit être uniquement le texte de l'avis, sans introduction ni fioritures.
    """

    try:
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "Tu es un assistant de rédaction d'avis pour Gallopin, une brasserie parisienne de luxe. Rédige des avis authentiques et élégants en français."},
                {"role": "user", "content": prompt_text}
            ]
        )
        review_text = completion.choices[0].message.content
        return jsonify({'review': review_text})
    except Exception as e:
        return jsonify({"error": f"Erreur lors de la génération de l'avis avec OpenAI : {str(e)}"}), 500


@app.route('/submit-feedback', methods=['POST'])
def submit_feedback():
    """ Enregistre un feedback interne pour un serveur. """
    data = request.json
    server_name = data.get('serverName')
    feedback_text = data.get('feedbackText')

    if not server_name or not feedback_text:
        return jsonify({"error": "Nom du serveur et feedback requis."}), 400

    new_feedback = Feedback(server_name=server_name, feedback_text=feedback_text)
    db.session.add(new_feedback)
    db.session.commit()
    return jsonify({"message": "Feedback enregistré avec succès."}), 201

@app.route('/admin-data')
def get_admin_data():
    """ Fournit les données pour le dashboard d'administration. """
    try:
        # Top des plats
        top_flavors_query = db.session.query(
            FlavorOption.text,
            ReviewSelection.count
        ).join(ReviewSelection).order_by(ReviewSelection.count.desc()).limit(10).all()
        top_flavors = [{"flavor": flavor, "count": count} for flavor, count in top_flavors_query]

        # Feedbacks récents
        recent_feedbacks_query = Feedback.query.order_by(Feedback.created_at.desc()).limit(20).all()
        recent_feedbacks = [{
            "server_name": f.server_name,
            "feedback_text": f.feedback_text,
            "created_at": f.created_at.strftime('%d/%m/%Y %H:%M')
        } for f in recent_feedbacks_query]

        return jsonify({
            "top_flavors": top_flavors,
            "recent_feedbacks": recent_feedbacks
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# --- Commandes CLI pour la gestion de la base de données ---

def populate_db():
    """ (Ré)initialise la base de données avec le menu de Gallopin. """
    print("Suppression des anciennes données...")
    ReviewSelection.query.delete()
    FlavorOption.query.delete()

    print("Ajout des plats du menu Gallopin...")
    menu_gallopin = [
        # Entrées
        {"text": "Les 6 huîtres 'Perles de l'Impératrice'", "category": "Entrées"},
        {"text": "Pâté en croûte du Gallopin", "category": "Entrées"},
        {"text": "Poireaux vinaigrette, œuf mimosa", "category": "Entrées"},
        {"text": "Velouté de saison", "category": "Entrées"},
        {"text": "Œuf parfait, champignons et lard", "category": "Entrées"},
        {"text": "Gravelax de saumon, crème acidulée", "category": "Entrées"},
        {"text": "Les 6 escargots de Bourgogne", "category": "Entrées"},

        # Poissons & Coquillages
        {"text": "Sole meunière (350g-400g)", "category": "Poissons & Coquillages"},
        {"text": "Saint-Jacques snackées, risotto crémeux", "category": "Poissons & Coquillages"},
        {"text": "Bar en croûte de sel, légumes de saison", "category": "Poissons & Coquillages"},
        {"text": "Tartare de daurade, agrumes et coriandre", "category": "Poissons & Coquillages"},

        # Viandes
        {"text": "Le foie de veau du Gallopin", "category": "Viandes"},
        {"text": "Filet de bœuf, sauce poivre", "category": "Viandes"},
        {"text": "Entrecôte (300g), frites maison", "category": "Viandes"},
        {"text": "Tartare de bœuf préparé", "category": "Viandes"},
        {"text": "Chateaubriand pour deux", "category": "Viandes"},
        {"text": "Volaille fermière rôtie, jus corsé", "category": "Viandes"},

        # Plats Végétariens
        {"text": "Risotto aux cèpes", "category": "Plats Végétariens"},
        {"text": "Légumes de saison rôtis, houmous maison", "category": "Plats Végétariens"},

        # Garnitures
        {"text": "Frites maison", "category": "Garnitures"},
        {"text": "Purée de pommes de terre", "category": "Garnitures"},
        {"text": "Haricots verts frais", "category": "Garnitures"},
        {"text": "Salade verte", "category": "Garnitures"},

        # Fromages
        {"text": "Assiette de fromages affinés", "category": "Fromages"},
        {"text": "Saint-Marcellin", "category": "Fromages"},

        # Desserts
        {"text": "Crème brûlée à la vanille Bourbon", "category": "Desserts"},
        {"text": "Profiteroles au chocolat chaud", "category": "Desserts"},
        {"text": "Tarte fine aux pommes, glace vanille", "category": "Desserts"},
        {"text": "Mousse au chocolat grand cru", "category": "Desserts"},
        {"text": "Baba au rhum ambré", "category": "Desserts"},
        {"text": "Café gourmand", "category": "Desserts"},
    ]

    for item in menu_gallopin:
        db.session.add(FlavorOption(text=item["text"], category=item["category"]))

    db.session.commit()
    print("Base de données peuplée avec succès !")

@app.cli.command("init-db")
def init_db_command():
    """ Commande pour initialiser la base de données. """
    with app.app_context():
        db.drop_all()
        db.create_all()
        populate_db()

if __name__ == '__main__':
    with app.app_context():
        db.create_all() # Crée les tables si elles n'existent pas
        # Vérifie si la base de données est vide avant de la peupler
        if not FlavorOption.query.first():
            print("La base de données est vide, peuplement initial...")
            populate_db()
    app.run(debug=True)
