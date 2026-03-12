import argparse
import json
import os
import re
import sqlite3
import sys
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
HISTORY_SIZE_TARGET = 20  # Nombre d'items à viser dans l'historique

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


def repopulate_history_from_archive(
    current_threads: dict, max_size: int, archive_path: Path, verbose=False
) -> dict:
    """
    Repopule l'historique des threads à partir des archives JSON si le nombre
    de threads actuel est inférieur à la taille maximale souhaitée.
    """
    needed = max_size - len(current_threads)
    if needed <= 0:
        return current_threads

    if not archive_path.exists():
        if verbose:
            print(
                f"  -> Le dossier d'archive JSON '{archive_path}' n'existe pas. Impossible de repeupler."
            )
        return current_threads

    # 1. Lister et trier les archives JSON par date (plus récent d'abord)
    # Le nom de fichier est 'YYYY-MM-DD HHhMMmSS - ... .json'
    archived_files = sorted(archive_path.glob("*.json"), reverse=True)

    if not archived_files:
        if verbose:
            print("  -> Aucune archive JSON trouvée pour le repeuplement.")
        return current_threads

    # 2. On utilise les titres pour vérifier les doublons
    existing_titles = {data["title"] for data in current_threads.values()}

    added_count = 0
    if verbose:
        print(f"  -> Recherche de {needed} item(s) à ré-injecter...")

    # 3. Itérer sur les archives et ajouter les manquants
    for json_file in archived_files:
        if added_count >= needed:
            break

        try:
            with json_file.open("r", encoding="utf-8") as f:
                archived_data = json.load(f)

            archived_title = archived_data.get("title")
            thread_id = archived_data.get("id")

            if not archived_title or not thread_id or archived_title in existing_titles:
                continue

            current_threads[thread_id] = archived_data
            existing_titles.add(archived_title)
            added_count += 1
            if verbose:
                print(f"    -> [INJECT] '{archived_title}' (ID: {thread_id})")
        except (json.JSONDecodeError, IOError) as e:
            if verbose:
                print(
                    f"    -> Erreur en lisant le fichier d'archive {json_file.name}: {e}"
                )

    if verbose and added_count > 0:
        print(
            f"  -> {added_count} item(s) ré-injecté(s). Total final : {len(current_threads)}."
        )

    return current_threads


def process_thread_export(thread_data, email, verbose=False):
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
            if file_date == update_time_str:
                if verbose:
                    print(f"  -> [EXISTE] {title_cleaned} (identique)")
                return
            elif file_date < update_time_str:
                # Le fichier existant est plus vieux : on le supprime pour le remplacer
                if verbose:
                    print(f"  -> [MAJ] {title_cleaned} (nouvelle version)")
                md_file.unlink(missing_ok=True)
                # On supprime aussi le JSON associé
                (JSON_BACKUP_PATH / md_file.with_suffix(".json").name).unlink(
                    missing_ok=True
                )
            elif file_date > update_time_str:
                # Le fichier existant est plus récent : on ne fait rien
                if verbose:
                    print(
                        f"  -> [SKIP] {title_cleaned} (version plus récente existante)"
                    )
                found_newer = True
            break

    if found_newer:
        return

    # --- ÉCRITURE DES FICHIERS ---
    base_name = f"{update_time_str} - {title_cleaned}"

    if verbose:
        print(f"  -> [EXPORT] {base_name}")

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


def force_vscode_reload(verbose=False):
    """
    Tente de forcer le rechargement de la fenêtre VSCode via AppleScript (macOS).
    Avertissement : Simule une interaction utilisateur et peut être instable.
    Remarque : ne fonctionne pas comme prévu : la liste de l'historique n'est pas réactualisée dans le logiciel,
    il faut quand même le fermer et le rouvrir pour voir les changements.
    """
    if sys.platform != "darwin":
        if verbose:
            print(
                "\nL'option --reload-vscode est uniquement supportée sur macOS et sera ignorée."
            )
        return

    if verbose:
        print("\nTentative de forcer le rechargement de la fenêtre VSCode...")

    script = """
    tell application "Visual Studio Code" to activate
    delay 0.2
    tell application "System Events"
        keystroke "p" using {command down, shift down}
        delay 0.5
        keystroke "Developer: Reload Window"
        delay 0.5
        keystroke return
    end tell
    """
    try:
        import subprocess

        subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        print(f"Échec de l'envoi de la commande de rechargement via AppleScript : {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Nettoyage et archivage de l'historique Gemini Code Assist."
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Mode verbeux")
    parser.add_argument(
        "-r",
        "--reload-vscode",
        action="store_true",
        help="Tenter de forcer le rechargement de la fenêtre VSCode via AppleScript (macOS uniquement).",
    )
    args = parser.parse_args()

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

        # Dictionnaire pour dédoublonner par titre, en gardant le plus récent
        # Format: { "titre_nettoyé": {"id": "id_du_thread", "data": {...}} }
        dedup_threads = {}

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
            process_thread_export(t_data, email, verbose=args.verbose)

            # 2. Logique de dédoublonnage pour la réécriture dans la base de données
            current_title = t_data["title"]
            if current_title not in dedup_threads or t_data.get(
                "update_time", ""
            ) > dedup_threads[current_title]["data"].get("update_time", ""):
                dedup_threads[current_title] = {"id": t_id, "data": t_data}

        # Reconstruire la map de threads avec les IDs d'origine pour la DB
        clean_threads = {item["id"]: item["data"] for item in dedup_threads.values()}

        # 3. Repeupler l'historique depuis l'archive si nécessaire
        if len(clean_threads) < HISTORY_SIZE_TARGET:
            if args.verbose:
                print(
                    f"\nHistorique actuel ({len(clean_threads)} items) est inférieur à {HISTORY_SIZE_TARGET}. "
                    "Tentative de repeuplement depuis l'archive."
                )
            clean_threads = repopulate_history_from_archive(
                clean_threads,
                HISTORY_SIZE_TARGET,
                JSON_BACKUP_PATH,
                verbose=args.verbose,
            )

        threads_root[email] = clean_threads

    # Finalisation
    full_data["geminiCodeAssist.chatThreads"] = threads_root

    # Application de l'update de la base de données
    try:
        cursor.execute(
            "UPDATE ItemTable SET value = ? WHERE key = ?", (json.dumps(full_data), KEY)
        )
        conn.commit()
    except sqlite3.OperationalError as e:
        if args.verbose:
            print(
                f"Erreur d'accès à la base de données (probablement verrouillée par VS Code) : {e}"
            )

    # Fermeture des connexions
    if client:
        client.close()
    conn.close()

    if args.reload_vscode:
        force_vscode_reload(verbose=args.verbose)

    print("Export et nettoyage terminés.")


if __name__ == "__main__":
    main()
