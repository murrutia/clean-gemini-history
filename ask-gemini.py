import os

from dotenv import load_dotenv
from google.genai import Client, errors, types

load_dotenv()

client = Client()

try:

    prompt = f"""
    Génère un titre court, pertinent et explicite en français de 50 caractères maximum pour la conversation suivante.
    Le titre doit résumer le sujet principal. Ne renvoie QUE le titre, sans guillemets ni fioritures.

    Début de la conversation :
    ---
    Après avoir remarqué que l'historique de mes conversations avec Gemini Code Assist dans VSCode était pollué
    par des doublons pour le même thread avec des titres entourés de ["Copy of", "\n"] j'ai voulu créé un script
    pour le nettoyer : c'est le script en contexte de cette discussion. Au passage, comme j'utilise Obsidian sur
    un NextCloud, je me suis dit que je pouvais sauvegarder mes conversations durablement (NextCloud) et les relire
    facilement avec l'interface d'Obsidian. J'ai créé ce script avec l'aide de Gemini, mais je ne suis pas complètement
    satisfait. Est-ce que tu peux l'analyser et voir si tu peux le nettoyer et le commenter correctement ?
    ---
    """

    r = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(
            temperature=0.0
        ),  # 0.0 pour un résultat stable/déterministe
    )
    print(r.text.strip())

except errors.APIError as e:
    print(f"  -> Erreur API Gemini : {e.message}")
except Exception as e:
    print(f"  -> Erreur inattendue lors de l'appel à Gemini : {e}")

client.close()
