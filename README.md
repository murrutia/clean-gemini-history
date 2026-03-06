# Clean Gemini History

Ce script Python a pour but de nettoyer et d'archiver l'historique des conversations de l'extension `Gemini Code Assist` dans VS Code.

**Fonctionnalités :**

-   **Nettoyage des titres :** Remplace les longs titres initiaux des conversations par des titres courts et pertinents générés par l'API Gemini.
-   **Dédoublonnage :** Gère les threads dupliqués (souvent préfixés par "Copy of") en ne conservant que la version la plus récente.
-   **Archivage pour Obsidian :** Exporte chaque conversation dans un fichier Markdown propre, directement utilisable dans un coffre Obsidian.
-   **Sauvegarde brute :** Conserve une copie JSON complète de chaque thread pour toute éventualité.
-   **Nettoyage de la base de données VS Code :** Met à jour la base de données de VS Code avec les titres nettoyés pour une expérience plus propre directement dans l'éditeur.

---

## Prérequis

-   Python 3.8+
-   Un accès à l'API Gemini de Google.

## Installation

1.  **Cloner le projet**

    ```bash
    git clone <votre-repo-url>
    cd clean-gemini-history
    ```

2.  **Créer l'environnement virtuel pour Python avec [uv](https://docs.astral.sh/uv/)**

    ```bash
    uv sync
    source .venv/bin/activate
    ```

4.  **Configurer les variables d'environnement**

    Copiez le fichier d'exemple et remplissez-le avec vos informations.

    ```bash
    cp .env.example .env
    ```

    Ouvrez le fichier `.env` et ajoutez votre clé API Gemini. Vous pouvez aussi surcharger les chemins par défaut vers votre base de données VS Code et votre coffre Obsidian si nécessaire.

## Utilisation

Pour lancer le script manuellement :

```bash
python3 clean-geminy.py
```

## Automatisation (macOS)

Pour exécuter le script automatiquement (par exemple, toutes les heures), vous pouvez utiliser `launchd`, le gestionnaire de services de macOS.

1.  **Adapter le fichier de configuration :**
    -   Renommez `clean-gemini.plist.example` en `com.user.cleangemini.plist`.
    -   Ouvrez ce fichier et **remplacez les chemins placeholders** par les chemins absolus corrects vers l'interpréteur Python de votre environnement virtuel et vers le script `clean-geminy.py`.

2.  **Installer l'agent :**
    -   Copiez le fichier `.plist` dans le dossier des agents `launchd` de votre utilisateur.
    ```bash
    cp com.user.cleangemini.plist ~/Library/LaunchAgents/
    ```

3.  **Charger l'agent :**
    -   Pour que le service soit pris en compte immédiatement (sans avoir à redémarrer).
    ```bash
    launchctl load ~/Library/LaunchAgents/com.user.cleangemini.plist
    ```

Le script s'exécutera maintenant en arrière-plan à l'intervalle défini dans le fichier `.plist`. Vous pouvez vérifier les logs de sortie et d'erreur dans `/tmp/` pour vous assurer que tout fonctionne comme prévu.
