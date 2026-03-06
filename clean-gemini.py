import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# Charge les variables d'environnement à partir d'un fichier .env
# Utile pour charger la clé GEMINI_API_KEY de manière sécurisée.
load_dotenv()

# --- CONFIGURATION ---
# Chemin vers la base de données d'état de VS Code
VSCODE_DB_PATH = Path(
    os.getenv(
        "VSCODE_DB_PATH",
        "~/Library/Application Support/Code/User/globalStorage/state.vscdb",
    )
).expanduser()

# Dossier racine de votre coffre Obsidian pour l'archive
OBSIDIAN_VAULT_PATH = Path(
    os.getenv("OBSIDIAN_VAULT_PATH", "~/Documents/Obsidian_Vault")
).expanduser()

# Sous-dossier pour la sauvegarde brute JSON
JSON_BACKUP_PATH = OBSIDIAN_VAULT_PATH / "raw_json"

KEY = "google.geminicodeassist"

# --- CONFIGURATION IA (Optionnel) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


def clean_filename(title: str) -> str:
    """Nettoie une chaîne pour qu'elle soit un nom de fichier valide."""
    title = re.sub(r'[\\/*?:"<>`|]', "", title)
    return title[:100].strip()


def clean_title(title: str) -> str:
    """Nettoie le titre pour l'affichage (supprime 'Copy of', guillemets, etc.)."""
    if not title:
        return "Sans titre"
    cleaned = re.sub(r'^(Copy of\s+|"|\\")+|("|\\")+$', "", title)
    return cleaned.replace("\n", " ").strip()


def get_thread_datetime(update_time):
    """Parse la date ISO 8601 avec ou sans millisecondes."""
    try:
        return datetime.strptime(update_time, "%Y-%m-%dT%H:%M:%S.%fZ")
    except ValueError:
        return datetime.strptime(update_time, "%Y-%m-%dT%H:%M:%SZ")


def generate_title_with_gemini(client, text: str) -> str | None:
    """
    Utilise l'API Gemini pour générer un titre concis et pertinent à partir d'un texte.
    Retourne le titre sous forme de chaîne, ou None si l'API n'est pas configurée ou échoue.
    """
    if not client:
        return None

    prompt = f"""
    Génère un titre court, pertinent et explicite en français de 50 caractères maximum pour la conversation suivante.
    Le titre doit résumer le sujet principal. Ne renvoie QUE le titre, sans guillemets ni fioritures.

    Début de la conversation :
    ---
    {text[:2000]}
    ---
    """
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.0
            ),  # 0.0 pour un résultat stable/déterministe
        )
        # Nettoyage de la réponse pour enlever d'éventuels guillemets ou markdown
        return response.text.strip().strip('"').strip("'")
    except errors.APIError as e:
        print(f"  -> Erreur API Gemini : {e.message}")
        return None
    except Exception as e:
        print(f"  -> Erreur inattendue lors de l'appel à Gemini : {e}")
        return None


def process_thread_export(thread_data, email):
    """
    Gère l'export d'un thread :
    1. Vérifie s'il existe déjà une version plus récente ou plus ancienne.
    2. Écrit les fichiers Markdown et JSON si nécessaire.
    """
    # Création des dossiers si nécessaire (équivalent à mkdir -p)
    OBSIDIAN_VAULT_PATH.mkdir(parents=True, exist_ok=True)
    JSON_BACKUP_PATH.mkdir(parents=True, exist_ok=True)

    update_time = thread_data.get("update_time", "2000-01-01T00:00:00.000Z")
    dt = get_thread_datetime(update_time)
    update_time_str = dt.strftime("%Y-%m-%d %Hh%Mm%S")

    title_cleaned = clean_filename(clean_title(thread_data.get("title", "Untitled")))

    # Stratégie de recherche de doublons sur le titre
    found_newer = False

    # On itère sur les fichiers .md existants dans le dossier
    for md_file in OBSIDIAN_VAULT_PATH.glob("*.md"):
        if " - " not in md_file.name:
            continue

        # On utilise .stem pour enlever l'extension, et on sépare la date du titre
        # .split(' - ', 1) est robuste, même si le titre contient des tirets
        file_date, file_title = md_file.stem.split(" - ", 1)

        if file_title == title_cleaned:
            if file_date < update_time_str:
                # Le fichier existant est plus vieux : on le supprime pour le remplacer
                md_file.unlink(missing_ok=True)
                # On supprime aussi le JSON associé
                (JSON_BACKUP_PATH / md_file.with_suffix(".json").name).unlink(
                    missing_ok=True
                )
            elif file_date > update_time_str:
                # Le fichier existant est plus récent : on ne fait rien
                found_newer = True
            break

    if found_newer:
        return

    # --- ÉCRITURE DES FICHIERS ---
    base_name = f"{update_time_str} - {title_cleaned}"

    # 1. Export Markdown
    md_path = OBSIDIAN_VAULT_PATH / f"{base_name}.md"
    write_markdown(md_path, thread_data, email)

    # 2. Export JSON (le thread complet tel quel)
    json_path = JSON_BACKUP_PATH / f"{base_name}.json"
    with json_path.open("w", encoding="utf-8") as f:
        json.dump(thread_data, f, indent=2, ensure_ascii=False)


def write_markdown(filepath: Path, thread_data: dict, email: str):
    """Génère le contenu Markdown compatible Obsidian."""
    title = clean_title(thread_data.get("title"))

    with filepath.open("w", encoding="utf-8") as f:
        f.write(
            f"""---
tags: gemini-archive
title: "{title[:100]}"
created: {thread_data.get('create_time')}
updated: {thread_data.get('update_time')}
---
> source: [./raw_json/{filepath.stem}.json]

"""
        )

        for msg in thread_data.get("history", []):
            role = "👤 **Moi**" if msg.get("entity") == "USER" else "🤖 **Gemini**"
            f.write(f"### {role}\n\n{msg.get('markdownText', '')}\n\n---\n\n")


def main():
    if not VSCODE_DB_PATH.exists():
        return

    # Initialisation du client Gemini et du cache de titres
    title_cache = {}
    client = None
    if GEMINI_API_KEY:
        try:
            # Le client est créé une seule fois et réutilisé
            # Il lira automatiquement la variable d'environnement GEMINI_API_KEY
            client = genai.Client()
        except Exception as e:
            print(f"Erreur lors de l'initialisation du client Gemini : {e}")
    else:
        print(
            "Avertissement: La variable d'environnement GEMINI_API_KEY n'est pas définie. "
            "La génération de titre par IA est ignorée."
        )

    conn = sqlite3.connect(VSCODE_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT value FROM ItemTable WHERE key = ?", (KEY,))
    row = cursor.fetchone()
    if not row:
        if client:
            client.close()
        return

    full_data = json.loads(row[0])
    threads_root = full_data.get("geminiCodeAssist.chatThreads", {})

    for email, threads_map in threads_root.items():
        print(f"Traitement de {email}...")

        # clean_threads contiendra la version finale des threads à réécrire dans la DB
        clean_threads = {}

        for t_id, t_data in list(threads_map.items()):
            original_title = t_data.get("title", "")
            canonical_content = clean_title(original_title)

            if canonical_content in title_cache:
                # Utiliser le titre déjà généré ou nettoyé depuis le cache
                t_data["title"] = title_cache[canonical_content]
            else:
                final_title = canonical_content
                # Tenter la génération par IA si le client est dispo et le contenu assez long
                if client and len(canonical_content.split()) > 10:
                    print(
                        f"  -> Génération du titre pour : '{canonical_content[:50]}...'"
                    )
                    generated_title = generate_title_with_gemini(
                        client, canonical_content
                    )
                    if generated_title:
                        final_title = generated_title
                        print(f"  -> Nouveau titre : '{final_title}'")

                # Mettre à jour le titre dans les données du thread
                t_data["title"] = final_title
                # Mettre en cache le résultat (titre généré ou titre nettoyé)
                title_cache[canonical_content] = final_title

            # 1. Exporter le thread (avec son titre potentiellement nouveau) vers les fichiers
            process_thread_export(t_data, email)

            # 2. Logique de dédoublonnage pour la réécriture dans la base de VS Code
            if t_data["title"] not in clean_threads or t_data.get(
                "update_time", ""
            ) > clean_threads.get(t_data["title"], {}).get("update_time", ""):
                clean_threads[t_data["title"]] = t_data

        threads_root[email] = clean_threads

    # Finalisation
    full_data["geminiCodeAssist.chatThreads"] = threads_root

    # Application de l'update de la base de données
    cursor.execute(
        "UPDATE ItemTable SET value = ? WHERE key = ?", (json.dumps(full_data), KEY)
    )
    conn.commit()

    # Fermeture des connexions
    if client:
        client.close()
    conn.close()
    print("Export et nettoyage terminés.")


if __name__ == "__main__":
    main()
