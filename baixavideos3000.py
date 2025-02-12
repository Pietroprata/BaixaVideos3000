import sys
import os
import json
import logging
import shutil
import time
from datetime import datetime
import re
import asyncio
import psutil
from logging.handlers import RotatingFileHandler
from PyQt5.QtCore import QTimer
from PyQt5.QtGui import QImage, QPixmap
from PyQt5.QtWidgets import QSystemTrayIcon
import requests

from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
                             QTabWidget, QLineEdit, QRadioButton, QButtonGroup, QPushButton, QComboBox,
                             QLabel, QTableWidget, QTableWidgetItem, QTextEdit, QFileDialog, QMessageBox,
                             QHeaderView, QDialog, QDialogButtonBox, QStyle, QProgressBar, QAction)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QFont, QIcon

from yt_dlp import YoutubeDL


from queue import PriorityQueue
from dataclasses import dataclass
from datetime import datetime

import shelve
from functools import lru_cache


# Versão atual do aplicativo (definida como 0.0.3)
CURRENT_VERSION = "0.0.3"


# ------------------------------
# Sistema de Filas com Prioridade
# ------------------------------
@dataclass
class DownloadPriority:
    HIGH = 1
    NORMAL = 2
    LOW = 3

class PriorityDownloadQueue:
    def __init__(self):
        self.queue = PriorityQueue()
        self.current_downloads = {}
        self.max_concurrent = 3

    def add_download(self, download_item, priority=DownloadPriority.NORMAL):
        timestamp = datetime.now().timestamp()
        self.queue.put((priority, timestamp, download_item))
        self.process_queue()

    def process_queue(self):
        while (not self.queue.empty() and 
               len(self.current_downloads) < self.max_concurrent):
            priority, _, item = self.queue.get()
            self.start_download(item)


#
#Performance - cache de download
#

class DownloadCache:
    def __init__(self, cache_file="download_cache"):
        self.cache_file = cache_file

    def save_to_cache(self, url, info):
        with shelve.open(self.cache_file) as cache:
            cache[url] = {
                'info': info,
                'timestamp': datetime.now().timestamp()
            }

    @lru_cache(maxsize=100)
    def get_from_cache(self, url):
        with shelve.open(self.cache_file) as cache:
            data = cache.get(url)
            if data and (datetime.now().timestamp() - data['timestamp']) < 86400:
                return data['info']
        return None

#
# Gerenciamento de memória
#

class MemoryManager:
    def __init__(self):
        self.memory_threshold = 85  # porcentagem
        self.check_interval = 300  # segundos

    def start_monitoring(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.check_memory_usage)
        self.timer.start(self.check_interval * 1000)

    def check_memory_usage(self):
        memory_use = psutil.virtual_memory().percent
        if memory_use > self.memory_threshold:
            self.clean_up_resources()

    def clean_up_resources(self):
        # Limpar downloads concluídos
        # Limpar cache antigo
        # Remover arquivos temporários
        pass


#
#SEGURANÇA - verificador de URL
#

class URLValidator:
    def __init__(self):
        self.patterns = {
            'youtube': r'^https?:\/\/(www\.)?(youtube\.com|youtu\.be)\/.+$',
            'instagram': r'^https?:\/\/(www\.)?instagram\.com\/(p|reel|tv)\/.+$',
            'twitch': r'^https?:\/\/(www\.)?twitch\.tv\/.+$'
        }

    def validate_url(self, url: str) -> tuple[bool, str]:
        for platform, pattern in self.patterns.items():
            if re.match(pattern, url):
                return True, platform
        return False, ""

    def sanitize_filename(self, filename: str) -> str:
        # Remove caracteres inválidos do nome do arquivo
        return re.sub(r'[<>:"/\\|?*]', '', filename)

#
#Sistema de Logs aprimorado
#

class EnhancedLogger:
    def __init__(self, log_file="downloads.log"):
        self.log_file = log_file
        self.setup_logger()

    def setup_logger(self):
        formatter = logging.Formatter(
            '%(asctime)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=5*1024*1024,  # 5MB
            backupCount=3
        )
        file_handler.setFormatter(formatter)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        logger = logging.getLogger()
        logger.addHandler(file_handler)
        logger.addHandler(console_handler)
        logger.setLevel(logging.INFO)

# -----------------------------------------------------------------------------
# Função para buscar atualizações automaticamente (comentada para uso futuro)
# -----------------------------------------------------------------------------
# def check_updates(parent):
#     try:
#         response = requests.get("https://api.github.com/repos/ReginaldoHorse/BaixaVideos3000/releases/latest", timeout=5)
#         if response.status_code == 200:
#             release_info = response.json()
#             latest_version = release_info.get("tag_name", "")
#             if latest_version and latest_version != CURRENT_VERSION:
#                 QMessageBox.information(parent, "Atualização Disponível",
#                                         f"Uma nova versão ({latest_version}) está disponível no GitHub.\n"
#                                         "Acesse https://github.com/ReginaldoHorse/BaixaVideos3000/ para atualizar.")
#     except Exception as e:
#         logging.error("Erro ao buscar atualizações: " + str(e))

# -----------------------------------------------------------------------------
# LogHandler para exibição na aba Logs
# -----------------------------------------------------------------------------
class LogHandler(logging.Handler):
    def __init__(self, widget: QTextEdit):
        super().__init__()
        self.widget = widget

    def emit(self, record):
        msg = self.format(record)
        self.widget.append(msg)

# -----------------------------------------------------------------------------
# Classe que representa cada download
# -----------------------------------------------------------------------------
class DownloadItem:
    def __init__(self, url: str, format_choice: str, resolution_choice: str):
        self.url = url
        self.format_choice = format_choice
        self.resolution_choice = resolution_choice
        self.progress = 0.0
        self.status = "Na fila"  # "Na fila", "Baixando", "Concluído", "Erro", "Cancelado"
        self.title = "Carregando..."
        self.id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self.added_at = datetime.now().strftime("%H:%M:%S %d/%m")
        self.cancelled = False
        self.file_path = ""  # Armazena o caminho do arquivo baixado

# -----------------------------------------------------------------------------
# Thread para realizar o download usando yt_dlp
# -----------------------------------------------------------------------------
class DownloadThread(QThread):
    progress_signal = pyqtSignal(str, float, str)  # id, progresso, status
    finished_signal = pyqtSignal(str)              # id

    def __init__(self, download_item: DownloadItem, download_folder: str):
        super().__init__()
        self.item = download_item
        self.download_folder = download_folder

    def run(self):
        def progress_hook(d: dict):
            if self.item.cancelled:
                raise Exception("Download cancelado pelo usuário.")
            if d.get('status') == 'downloading':
                total = d.get('total_bytes') or d.get('total_bytes_estimate', 0)
                if total:
                    downloaded = d.get('downloaded_bytes', 0)
                    progress = (downloaded / total) * 100
                    # Emite o progresso apenas se for maior que o atual para evitar "voltas"
                    if progress >= self.item.progress:
                        self.item.progress = progress
                        self.progress_signal.emit(self.item.id, progress, "Baixando")
            elif d.get('status') == 'finished':
                self.progress_signal.emit(self.item.id, 100.0, "Processando")

        ydl_opts = {
            'outtmpl': os.path.join("temp_downloads", '%(title)s.%(ext)s'),
            'progress_hooks': [progress_hook],
            'retries': 10,
            'fragment_retries': 10,
            'skip_unavailable_fragments': False,
            'nocheckcertificate': True,
            'http_chunk_size': 1024 * 1024,
        }
        if "twitch.tv" in self.item.url.lower():
            ydl_opts['concurrent_fragment_downloads'] = 4
        if self.item.format_choice.upper() == "MÚSICA - MP3":
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }]
        else:
            ydl_opts['format'] = {
                "Melhor Qualidade": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
                "8K": "bestvideo[height<=4320][ext=mp4]+bestaudio[ext=m4a]/best[height<=4320][ext=mp4]",
                "4K": "bestvideo[height<=2160][ext=mp4]+bestaudio[ext=m4a]/best[height<=2160][ext=mp4]",
                "1080p": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]",
                "720p": "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]",
                "360p": "bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]"
            }.get(self.item.resolution_choice, "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]")
        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(self.item.url, download=False)
                self.item.title = info.get('title', 'Unknown Title')
                self.progress_signal.emit(self.item.id, 0, "Baixando")
                ydl.download([self.item.url])
                filename = ydl.prepare_filename(info)
                ext = "mp3" if self.item.format_choice.upper() == "MÚSICA - MP3" else "mp4"
                final_file = filename.rsplit(".", 1)[0] + f".{ext}"
                for _ in range(5):
                    if os.path.exists(final_file):
                        break
                    time.sleep(1)
                if os.path.exists(final_file):
                    destino = os.path.join(self.download_folder, os.path.basename(final_file))
                    shutil.move(final_file, destino)
                    self.item.file_path = destino
                    self.item.status = "Concluído"
                    self.progress_signal.emit(self.item.id, 100.0, "Concluído")
                else:
                    self.item.status = "Erro: Arquivo não encontrado"
                    self.progress_signal.emit(self.item.id, self.item.progress, self.item.status)
        except Exception as e:
            if "cancelado" in str(e).lower():
                self.item.status = "Cancelado"
            else:
                self.item.status = f"Erro: {e}"
            self.progress_signal.emit(self.item.id, self.item.progress, self.item.status)
        self.finished_signal.emit(self.item.id)

# -----------------------------------------------------------------------------
# Diálogo de Configurações (aba Configurações)
# -----------------------------------------------------------------------------
class ConfigDialog(QDialog):
    def __init__(self, current_folder: str, current_theme: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configurações")
        self.setModal(True)
        self.resize(400, 200)
        layout = QFormLayout()
        self.folder_edit = QLineEdit(current_folder)
        self.folder_edit.setReadOnly(True)
        btn_change = QPushButton("Alterar Pasta")
        btn_change.clicked.connect(self.change_folder)
        h_layout = QHBoxLayout()
        h_layout.addWidget(self.folder_edit)
        h_layout.addWidget(btn_change)
        layout.addRow("Pasta de Download:", h_layout)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Escuro", "Claro"])
        self.theme_combo.setCurrentText(current_theme)
        layout.addRow("Tema:", self.theme_combo)
        
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)
        layout.addRow(self.buttonBox)
        self.setLayout(layout)

    def change_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecione a pasta de download", self.folder_edit.text())
        if folder:
            self.folder_edit.setText(folder)

    def get_settings(self):
        return self.folder_edit.text(), self.theme_combo.currentText()

# -----------------------------------------------------------------------------
# Janela Principal com UI/UX Moderno e 3 Abas: Downloads, Logs e Configurações
# -----------------------------------------------------------------------------
class DownloadApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Baixa Videos 3000 by Reginaldo Horse")
        self.resize(800, 600)
        self.setWindowIcon(QIcon('ico.ico'))
        self.setFont(QFont("Segoe UI", 10))
        self.download_folder = self.load_config()
        self.current_theme = "Escuro"  # Tema padrão
        if not os.path.exists("temp_downloads"):
            os.makedirs("temp_downloads")
        self.downloads = {}   # {id: DownloadItem}
        self.threads = {}     # {id: DownloadThread}
        self.init_ui()
        self.setup_logging()
        self.apply_dark_theme()  # Inicia com tema Escuro
        # check_updates(self)  # Função de atualização comentada para uso futuro

    def load_config(self):
        config_file = "downloader_config.json"
        default_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        if os.path.exists(config_file):
            try:
                with open(config_file, "r") as f:
                    config = json.load(f)
                    return config.get("download_path", default_folder)
            except Exception as e:
                logging.error(f"Erro ao ler configuração: {e}")
        self.save_config(default_folder)
        return default_folder

    def save_config(self, path):
        config = {"download_path": path}
        try:
            with open("downloader_config.json", "w") as f:
                json.dump(config, f)
        except Exception as e:
            logging.error(f"Erro ao salvar configuração: {e}")

    def init_ui(self):
        # Removida a barra de menus superior para evitar redundância.
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        self.init_downloads_tab()
        self.init_logs_tab()
        self.init_config_tab()

    def init_downloads_tab(self):
        downloads_widget = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        header = QLabel("Baixa Vídeos 3000")
        header.setFont(QFont("Segoe UI", 20, QFont.Bold))
        header.setAlignment(Qt.AlignCenter)
        layout.addWidget(header)

        form_layout = QFormLayout()
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Insira a URL do vídeo...")
        form_layout.addRow("Link do Vídeo:", self.url_edit)

        self.format_group = QButtonGroup()
        h_format = QHBoxLayout()
        self.radio_mp4 = QRadioButton("Vídeo - MP4")
        self.radio_mp3 = QRadioButton("Música - MP3")
        self.radio_mp4.setChecked(True)
        self.format_group.addButton(self.radio_mp4)
        self.format_group.addButton(self.radio_mp3)
        h_format.addWidget(self.radio_mp4)
        h_format.addWidget(self.radio_mp3)
        form_layout.addRow("Formato:", h_format)

        self.resolution_combo = QComboBox()
        self.resolution_combo.addItems(["Melhor Qualidade", "8K", "4K", "1080p", "720p", "360p"])
        form_layout.addRow("Resolução:", self.resolution_combo)
        layout.addLayout(form_layout)

        self.radio_mp3.toggled.connect(self.toggle_resolution)

        self.add_button = QPushButton("Adicionar à Fila")
        self.add_button.setFixedHeight(45)
        layout.addWidget(self.add_button, alignment=Qt.AlignCenter)
        self.add_button.clicked.connect(self.add_download)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Título", "Formato", "Resolução", "Progresso", "Status", "Adicionado", "Ação"])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        header_table = self.table.horizontalHeader()
        header_table.setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)

        action_layout = QHBoxLayout()
        self.btn_clear = QPushButton("Limpar Finalizados")
        self.btn_retry = QPushButton("Reiniciar Download")
        self.btn_remove = QPushButton("Remover Selecionado")
        self.btn_cancel = QPushButton("Cancelar Download")
        for btn in [self.btn_clear, self.btn_retry, self.btn_remove, self.btn_cancel]:
            btn.setFixedHeight(40)
            action_layout.addWidget(btn)
        self.btn_clear.clicked.connect(self.clear_completed)
        self.btn_retry.clicked.connect(self.retry_download)
        self.btn_remove.clicked.connect(self.remove_selected)
        self.btn_cancel.clicked.connect(self.cancel_download)
        layout.addLayout(action_layout)

        downloads_widget.setLayout(layout)
        self.tabs.addTab(downloads_widget, "Downloads")

    def init_logs_tab(self):
        logs_widget = QWidget()
        layout = QVBoxLayout()
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)
        btn_layout = QHBoxLayout()
        btn_clear_logs = QPushButton("Limpar Logs")
        btn_clear_logs.clicked.connect(lambda: self.log_text.clear())
        btn_export_log = QPushButton("Exportar Log")
        btn_export_log.clicked.connect(self.export_log)
        btn_layout.addWidget(btn_clear_logs)
        btn_layout.addWidget(btn_export_log)
        layout.addLayout(btn_layout)
        logs_widget.setLayout(layout)
        self.tabs.addTab(logs_widget, "Logs")

    def init_config_tab(self):
        config_widget = QWidget()
        layout = QFormLayout()
        self.folder_edit = QLineEdit(self.download_folder)
        self.folder_edit.setReadOnly(True)
        btn_change = QPushButton("Alterar Pasta")
        btn_change.clicked.connect(self.change_folder)
        h_layout = QHBoxLayout()
        h_layout.addWidget(self.folder_edit)
        h_layout.addWidget(btn_change)
        layout.addRow("Pasta de Download:", h_layout)
        
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(["Escuro", "Claro"])
        self.theme_combo.setCurrentText(self.current_theme)
        layout.addRow("Tema:", self.theme_combo)
        
        btn_apply = QPushButton("Aplicar Configurações")
        btn_apply.clicked.connect(self.apply_config)
        layout.addRow(btn_apply)
        
        config_widget.setLayout(layout)
        self.tabs.addTab(config_widget, "Configurações")

    def toggle_resolution(self):
        if self.radio_mp3.isChecked():
            self.resolution_combo.setEnabled(False)
        else:
            self.resolution_combo.setEnabled(True)

    def open_config_dialog(self):
        dialog = ConfigDialog(self.download_folder, self.current_theme, self)
        if dialog.exec_() == QDialog.Accepted:
            new_folder, theme = dialog.get_settings()
            self.download_folder = new_folder
            self.current_theme = theme
            self.save_config(new_folder)
            logging.info(f"Pasta de download alterada para: {new_folder}")
            if theme == "Escuro":
                self.apply_dark_theme()
            else:
                self.apply_light_theme()

    def apply_config(self):
        self.download_folder = self.folder_edit.text()
        self.current_theme = self.theme_combo.currentText()
        self.save_config(self.download_folder)
        logging.info(f"Pasta de download alterada para: {self.download_folder}")
        if self.current_theme == "Escuro":
            self.apply_dark_theme()
        else:
            self.apply_light_theme()
        QMessageBox.information(self, "Configurações", "Configurações aplicadas com sucesso.")

    def change_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Selecione a pasta de download", self.download_folder)
        if folder:
            self.download_folder = folder
            self.folder_edit.setText(folder)
            self.save_config(folder)
            logging.info(f"Pasta de download alterada para: {folder}")

    def export_log(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(self, "Exportar Log", "", "Text Files (*.txt)", options=options)
        if file_name:
            with open(file_name, "w", encoding="utf-8") as f:
                f.write(self.log_text.toPlainText())
            QMessageBox.information(self, "Exportar Log", "Log exportado com sucesso.")

    def setup_logging(self):
        self.log_handler = LogHandler(self.log_text)
        formatter = logging.Formatter("[%(asctime)s] %(levelname)s: %(message)s")
        self.log_handler.setFormatter(formatter)
        logging.getLogger().addHandler(self.log_handler)
        logging.getLogger().setLevel(logging.INFO)

    # ---------------- Temas e Estilo Moderno ----------------
    def apply_dark_theme(self):
        style = """
        QMainWindow { background-color: #121212; }
        QWidget { background-color: #121212; color: #e0e0e0; }
        QLineEdit, QComboBox, QTableWidget, QTextEdit {
            background-color: #1e1e1e;
            border: 1px solid #333;
            padding: 6px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11pt;
            color: #e0e0e0;
        }
        QPushButton {
            background-color: #E53935;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11pt;
        }
        QPushButton:hover { background-color: #D32F2F; }
        QTabWidget::pane { border: none; }
        QTabBar::tab {
            background-color: #1e1e1e;
            padding: 10px 15px;
            min-width: 100px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11pt;
        }
        QTabBar::tab:selected { background-color: #E53935; }
        QHeaderView::section {
            background-color: #1e1e1e;
            border: 1px solid #333;
            padding: 6px;
        }
        QToolBar { background-color: #1e1e1e; border: none; }
        QProgressBar {
            background-color: #333;
            border: 1px solid #555;
            text-align: center;
            color: white;
        }
        QProgressBar::chunk {
            background-color: #E53935;
        }
        """
        self.setStyleSheet(style)

    def apply_light_theme(self):
        style = """
        QMainWindow { background-color: #f5f5f5; }
        QWidget { background-color: #f5f5f5; color: #333; }
        QLineEdit, QComboBox, QTableWidget, QTextEdit {
            background-color: white;
            border: 1px solid #ccc;
            padding: 6px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11pt;
            color: #333;
        }
        QPushButton {
            background-color: #E53935;
            color: white;
            border: none;
            padding: 8px 16px;
            border-radius: 4px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11pt;
        }
        QPushButton:hover { background-color: #D32F2F; }
        QTabWidget::pane { border: none; }
        QTabBar::tab {
            background-color: white;
            padding: 10px 15px;
            min-width: 100px;
            font-family: 'Segoe UI', sans-serif;
            font-size: 11pt;
        }
        QTabBar::tab:selected { background-color: #E53935; }
        QHeaderView::section {
            background-color: #e0e0e0;
            border: 1px solid #ccc;
            padding: 6px;
        }
        QToolBar { background-color: white; border: none; }
        QProgressBar {
            background-color: #ccc;
            border: 1px solid #aaa;
            text-align: center;
            color: #333;
        }
        QProgressBar::chunk {
            background-color: #E53935;
        }
        """
        self.setStyleSheet(style)

    def toggle_theme(self):
        if self.current_theme == "Escuro":
            self.apply_light_theme()
            self.current_theme = "Claro"
        else:
            self.apply_dark_theme()
            self.current_theme = "Escuro"
    # ---------------------------------------------------------

    # ---------------- Operações de Download ----------------
    def add_download(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Aviso", "Por favor, insira a URL do vídeo.")
            return
        fmt = "Vídeo - MP4" if self.radio_mp4.isChecked() else "Música - MP3"
        resolution = self.resolution_combo.currentText()
        if "instagram.com" in url.lower() and "/p/" in url.lower() and "/reel/" not in url.lower():
            url = url.replace("/p/", "/reel/")
            logging.info("Link de Instagram convertido para Reels.")
        item = DownloadItem(url, fmt, resolution)
        self.downloads[item.id] = item

        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(item.title))
        self.table.setItem(row, 1, QTableWidgetItem(item.format_choice))
        self.table.setItem(row, 2, QTableWidgetItem(item.resolution_choice))
        progress_bar = QProgressBar()
        progress_bar.setRange(0, 100)
        progress_bar.setValue(0)
        progress_bar.setFormat("%p%")
        progress_bar.setStyleSheet("QProgressBar::chunk { background-color: #41e535; }")
        self.table.setCellWidget(row, 3, progress_bar)
        self.table.setItem(row, 4, QTableWidgetItem(item.status))
        self.table.setItem(row, 5, QTableWidgetItem(item.added_at))
        btn_open = QPushButton("Abrir")
        btn_open.setEnabled(False)
        btn_open.clicked.connect(lambda _, id=item.id: self.open_file(id))
        self.table.setCellWidget(row, 6, btn_open)
        self.table.setRowHeight(row, 40)

        thread = DownloadThread(item, self.download_folder)
        thread.progress_signal.connect(self.update_download)
        thread.finished_signal.connect(self.download_finished)
        self.threads[item.id] = thread
        thread.start()

        self.url_edit.clear()
        logging.info(f"Download adicionado: {url}")

    @pyqtSlot(str, float, str)
    def update_download(self, download_id: str, progress: float, status: str):
        item = self.downloads.get(download_id)
        if not item:
            return
        item.progress = progress
        item.status = status
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 5)
            if cell and cell.text() == item.added_at:
                self.table.setItem(row, 0, QTableWidgetItem(item.title))
                prog_widget = self.table.cellWidget(row, 3)
                if prog_widget:
                    if status == "Processando":
                        prog_widget.setValue(100)
                    else:
                        # Atualiza somente se o novo valor for maior para evitar "voltas"
                        if int(progress) > prog_widget.value():
                            prog_widget.setValue(int(progress))
                self.table.setItem(row, 4, QTableWidgetItem(item.status))
                btn_open = self.table.cellWidget(row, 6)
                if item.status == "Concluído":
                    btn_open.setEnabled(True)
                else:
                    btn_open.setEnabled(False)
                break

    @pyqtSlot(str)
    def download_finished(self, download_id: str):
        logging.info(f"Download finalizado: {download_id}")

    def open_file(self, download_id: str):
        item = self.downloads.get(download_id)
        if item and item.status == "Concluído" and os.path.exists(item.file_path):
            try:
                os.startfile(item.file_path)
            except Exception as e:
                QMessageBox.warning(self, "Erro", f"Não foi possível abrir o arquivo:\n{e}")
        else:
            QMessageBox.information(self, "Abrir", "Arquivo não disponível.")

    def clear_completed(self):
        remove_ids = [id for id, item in self.downloads.items() if item.status in ("Concluído", "Erro", "Cancelado") or item.status.startswith("Erro")]
        rows_to_remove = []
        for row in range(self.table.rowCount()):
            status_item = self.table.item(row, 4)
            if status_item and status_item.text() in ("Concluído", "Erro", "Cancelado"):
                rows_to_remove.append(row)
        for row in sorted(rows_to_remove, reverse=True):
            self.table.removeRow(row)
        for rid in remove_ids:
            if rid in self.downloads:
                del self.downloads[rid]
        logging.info("Downloads finalizados removidos.")

    def remove_selected(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Informação", "Nenhum item selecionado.")
            return
        row = selected[0].row()
        added = self.table.item(row, 5).text()
        remove_id = None
        for id, item in self.downloads.items():
            if item.added_at == added:
                remove_id = id
                break
        if remove_id:
            del self.downloads[remove_id]
            self.table.removeRow(row)
            logging.info(f"Download removido: {remove_id}")

    def retry_download(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Informação", "Nenhum item selecionado para reiniciar.")
            return
        row = selected[0].row()
        added = self.table.item(row, 5).text()
        for id, item in self.downloads.items():
            if item.added_at == added:
                if item.status.startswith("Erro"):
                    item.status = "Na fila"
                    item.progress = 0.0
                    item.cancelled = False
                    thread = DownloadThread(item, self.download_folder)
                    thread.progress_signal.connect(self.update_download)
                    thread.finished_signal.connect(self.download_finished)
                    self.threads[item.id] = thread
                    thread.start()
                    logging.info(f"Reiniciando download: {item.url}")
                else:
                    QMessageBox.information(self, "Informação", "Somente downloads com erro podem ser reiniciados.")
                break

    def cancel_download(self):
        selected = self.table.selectedItems()
        if not selected:
            QMessageBox.information(self, "Informação", "Nenhum item selecionado para cancelar.")
            return
        row = selected[0].row()
        added = self.table.item(row, 5).text()
        for id, item in self.downloads.items():
            if item.added_at == added:
                item.cancelled = True
                item.status = "Cancelado"
                self.update_download(item.id, item.progress, item.status)
                logging.info(f"Download cancelado: {item.url}")
                break
# -------------------------------------
# Barra de Progresso Aprimorada
# ------------------------------------            
class EnhancedProgressBar(QProgressBar):
    def __init__(self):
        super().__init__()
        self.setStyleSheet("""
            QProgressBar {
                border: 2px solid #555;
                border-radius: 5px;
                text-align: center;
                height: 25px;
            }
            QProgressBar::chunk {
                background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #E53935, stop:1 #D32F2F);
                border-radius: 3px;
            }
        """)
        
    def setProgress(self, progress, speed=None, eta=None):
        self.setValue(int(progress))
        if speed and eta:
            self.setFormat(f"{progress:.1f}% - {speed}/s - ETA: {eta}")
        else:
            self.setFormat(f"{progress:.1f}%")

#-------------------------------
# Mini player para preview 
#-----------------------------------
class VideoPreviewWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout()
        self.preview_label = QLabel()
        self.preview_label.setMinimumSize(320, 180)
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.preview_label)
        self.setLayout(self.layout)

    def update_preview(self, thumbnail_url):
        # Baixar e exibir thumbnail do vídeo
        response = requests.get(thumbnail_url)
        image = QImage.fromData(response.content)
        pixmap = QPixmap.fromImage(image).scaled(320, 180, Qt.KeepAspectRatio)
        self.preview_label.setPixmap(pixmap)

#
# Gerenciador de notificações
#

class NotificationSystem:
    def __init__(self):
        self.notification_icon = QSystemTrayIcon()
        self.setup_tray_icon()

    def setup_tray_icon(self):
        icon = QIcon('ico.ico')
        self.notification_icon.setIcon(icon)
        self.notification_icon.setVisible(True)

    def notify(self, title, message, level="info"):
        icon_map = {
            "info": QSystemTrayIcon.Information,
            "warning": QSystemTrayIcon.Warning,
            "error": QSystemTrayIcon.Critical,
            "success": QSystemTrayIcon.Information
        }
        self.notification_icon.showMessage(
            title,
            message,
            icon_map.get(level, QSystemTrayIcon.Information),
            3000
        )

#
# Detector Automático de Qualidade
#

class QualityDetector:
    def __init__(self):
        self.formats = {}

    async def detect_formats(self, url):
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True
        }
        
        with YoutubeDL(ydl_opts) as ydl:
            try:
                info = await self.run_ydl(ydl, url)
                formats = info.get('formats', [])
                return self.parse_formats(formats)
            except Exception as e:
                logging.error(f"Erro ao detectar formatos: {e}")
                return []

    def parse_formats(self, formats):
        parsed = []
        for fmt in formats:
            quality = {
                'format_id': fmt.get('format_id'),
                'ext': fmt.get('ext'),
                'height': fmt.get('height'),
                'filesize': fmt.get('filesize'),
                'tbr': fmt.get('tbr')
            }
            parsed.append(quality)
        return parsed

    @staticmethod
    async def run_ydl(ydl, url):
        return await asyncio.get_event_loop().run_in_executor(
            None, lambda: ydl.extract_info(url, download=False)
        )
# -----------------------------------------------------------------------------
# Execução da Aplicação
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DownloadApp()
    window.show()
    sys.exit(app.exec_())
