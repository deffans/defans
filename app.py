# -*- coding: utf-8 -*-
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
from flask import Flask, request, render_template_string, redirect, jsonify
import sqlite3
import hashlib
import smtplib
import os
import re
import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types as genai_types
from email.mime.text import MIMEText
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# 1. YAPILANDIRMA
# ─────────────────────────────────────────────────────────────────
GEMINI_API_KEY         = "AIzaSyCVIUcf3vuxSk5_pI-ijBF3f60wBuMiJy0"
GMAIL_ADRESIM          = "rumeyysauslu@gmail.com"
GMAIL_UYGULAMA_SIFRESI = "ckkc zbwk xgfp apws"
BILGI_GIDECEK_MAIL     = "rumeyysauslu@gmail.com"

ai_client = genai.Client(api_key=GEMINI_API_KEY)
app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────
# 2. VERİTABANI
# ─────────────────────────────────────────────────────────────────
def get_db():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    conn = sqlite3.connect(os.path.join(base_dir, 'news.db'))
    conn.row_factory = sqlite3.Row
    return conn

with get_db() as _c:
    _c.execute('''CREATE TABLE IF NOT EXISTS reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        content TEXT, email TEXT, status TEXT,
        score INTEGER, risk TEXT, hash TEXT,
        aciklama TEXT, ai_detay TEXT, created_at TEXT)''')
    try:
        _c.execute("ALTER TABLE reports ADD COLUMN ai_detay TEXT")
    except:
        pass

# ─────────────────────────────────────────────────────────────────
# 3. GELİŞTİRİLMİŞ AI ANALİZ FONKSİYONU
# ─────────────────────────────────────────────────────────────────
def analiz_et_ai(text):
    try:
        prompt = f"""Sen Türkiye'nin en deneyimli teyit gazetecisi ve dezenformasyon uzmanısın.
ÖNEMLİ GÖREV: Verilen metindeki iddiaların güncelliğini ve doğruluğunu internette araştır. Güncel takım transferleri, güncel olaylar vb. konularda arama motorunu kullanarak teyit et.

Aşağıdaki Türkçe metni şu kriterlere göre titizlikle değerlendir:

KONTROL KRİTERLERİ:
1. GÜNCEL DOĞRULUK: Kişilerin güncel durumları, son haberler veya mevcut takımları internetteki son verilerle uyuşuyor mu?
2. FİZİKSEL/BİLİMSEL MÜMKÜNLÜK: Bilim yasalarıyla çelişiyor mu?
3. BİLİNEN OLGULARLA ÇELIŞME: Herkesçe bilinen gerçeklerle çelişiyor mu?
4. ABARTMA/SENSASYONELLEŞME: Korku/panik yaratmak için abartılmış mı?
5. KAYNAK GÜVENİLİRLİĞİ: Kaynak belirtilmiş mi, güvenilir mi?

PUANLAMA KURALLARI:
- 0-30  → KESİNLİKLE YANLIŞ (fiziksel olarak imkansız, güncel durumla tamamen çelişen veya uydurma)
- 31-55 → ŞÜPHELİ (kaynak yok veya doğrulanamıyor)
- 56-75 → MUHTEMELEN DOĞRU (makul ama kesin kanıt eksik)
- 76-100 → GÜVENİLİR (mantıklı, güncel internet kaynaklarıyla ve gerçeklerle örtüşüyor)

Yanıtını şu formatta ver:
DETAY: [İnternette yaptığın araştırmaya göre 2-3 cümle analiz açıklaması. Örneğin: "İnternette yaptığım güncel araştırmalara göre Marco Asensio şu anda Fenerbahçe'de değil, PSG takımında forma giymektedir."]
PUAN: [0-100 arası tam sayı]

Metin: {text}"""

        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=genai_types.GenerateContentConfig(
                temperature=0.2,
                tools=[genai_types.Tool(googleSearch=genai_types.GoogleSearch())]
            )
        )
        raw = response.text.strip()
        print("[AI Ham Cevap]: " + repr(raw))

        # DETAY çek
        detay_match = re.search(r'DETAY:\s*(.+?)(?=PUAN:|$)', raw, re.DOTALL)
        ai_detay = detay_match.group(1).strip() if detay_match else ""

        # PUAN çek
        puan_match = re.search(r'PUAN:\s*(\d{1,3})', raw)
        if not puan_match:
            puan_match = re.search(r'(\d{1,3})(?=\D*$)', raw)
        if not puan_match:
            raise ValueError(f"Puan çıkarılamadı: {raw!r}")

        score = max(0, min(100, int(puan_match.group(1))))

        if score > 75:
            risk     = "GÜVENLİ"
            aciklama = "İçerik büyük olasılıkla doğru ve güvenilirdir."
        elif score > 55:
            risk     = "MUHTEMELEN DOĞRU"
            aciklama = "İçerik makul görünüyor ancak ek kaynak doğrulaması önerilir."
        elif score > 30:
            risk     = "ŞÜPHELİ"
            aciklama = "İçerik doğrulanamıyor veya şüpheli ifadeler barındırıyor."
        else:
            risk     = "TEHLİKELİ (YANLIŞ BİLGİ)"
            aciklama = "İçerik yüksek ihtimalle yanlış bilgi, uydurma veya dezenformasyon."

        return score, risk, aciklama, ai_detay

    except Exception as e:
        print("[AI HATA]: " + str(e))
        return 50, "ANALİZ HATASI", f"Hata: {e}", ""

# ─────────────────────────────────────────────────────────────────
# 4. E-POSTA
# ─────────────────────────────────────────────────────────────────
def mail_gonder(icerik, skor, risk, aciklama, gonderen):
    try:
        msg = MIMEText(f"""DEFANS PRO - Analiz Raporu
{'='*40}
Gönderen : {gonderen}
İçerik   : {icerik[:300]}...
Güven Puanı: {skor}/100
Risk: {risk}
Açıklama: {aciklama}
Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}""", _charset='utf-8')
        msg['Subject'] = "DEFANS PRO Analiz Raporu: " + risk
        msg['From']    = GMAIL_ADRESIM
        msg['To']      = BILGI_GIDECEK_MAIL
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_ADRESIM, GMAIL_UYGULAMA_SIFRESI)
            s.send_message(msg)
    except Exception as e:
        print("[MAIL HATA]: " + str(e))

# ─────────────────────────────────────────────────────────────────
# 5. URL & GÖRSEL ANALİZ FONKSİYONLARI
# ─────────────────────────────────────────────────────────────────
TWITTER_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

def url_icerik_cek(url):
    """URL'den metin çeker. Twitter/X için oEmbed API kullanır."""
    url = url.strip()
    try:
        # Twitter / X.com
        if 'twitter.com' in url or 'x.com' in url:
            oembed = requests.get(
                f'https://publish.twitter.com/oembed?url={url}&omit_script=true',
                timeout=10, headers=TWITTER_HEADERS
            )
            if oembed.status_code == 200:
                data = oembed.json()
                soup = BeautifulSoup(data.get('html', ''), 'html.parser')
                metin = soup.get_text(separator=' ', strip=True)
                yazar = data.get('author_name', '')
                return metin, f"Twitter/@{yazar}"
            # oEmbed basarisiz → direkt scrape dene
        # Genel URL
        resp = requests.get(url, timeout=10, headers=TWITTER_HEADERS)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'aside', 'header']):
            tag.decompose()
        # Başlık + makale metni
        title = soup.find('title')
        baslik = title.get_text(strip=True) if title else ''
        paragraphs = soup.find_all(['p', 'h1', 'h2', 'h3', 'article'])
        govde = ' '.join(p.get_text(separator=' ', strip=True) for p in paragraphs)[:3000]
        metin = (baslik + ' ' + govde).strip()
        return metin, url
    except Exception as e:
        return None, str(e)

def gorsel_analiz_et(image_bytes, mime_type):
    """Görseli Gemini Vision ile analiz eder — metin çıkarır ve doğruluk puanı verir."""
    try:
        prompt = (
            "Sen bir teyit gazetecisisin ve dezenformasyon uzmanisın.\n"
            "Bu gorselde ne yazıyor? Gorseldeki tum metni oku.\n"
            "Sonra bu metni yalan haber, yanlis bilgi ve manipulasyon acisindan degerlendir.\n\n"
            "Asagidaki formatta yaz:\n"
            "OKUNAN METIN: [gorseldeki metnin tamami]\n"
            "DETAY: [2-3 cumle analiz]\n"
            "PUAN: [0-100 arasi sayi — 0=kesinlikle yanlis, 100=guvenilir]"
        )
        response = ai_client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[
                genai_types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                prompt
            ]
        )
        raw = response.text.strip()
        print("[GORSEL AI]: " + repr(raw[:200]))

        # Okunan metni cek
        m = re.search(r'OKUNAN METIN:\s*(.+?)(?=DETAY:|PUAN:|$)', raw, re.DOTALL)
        okunan = m.group(1).strip() if m else ''

        # DETAY
        d = re.search(r'DETAY:\s*(.+?)(?=PUAN:|$)', raw, re.DOTALL)
        ai_detay = d.group(1).strip() if d else ''

        # PUAN
        p = re.search(r'PUAN:\s*(\d{1,3})', raw)
        if not p:
            p = re.search(r'(\d{1,3})(?=\D*$)', raw)
        score = max(0, min(100, int(p.group(1)))) if p else 50

        if score > 75:
            risk = 'GUVENLI'; aciklama = 'Gorsel icerik buyuk olasilikla dogru ve guvenilirdir.'
        elif score > 55:
            risk = 'MUHTEMELEN DOGRU'; aciklama = 'Makul gorunuyor ancak dogrulama onerilir.'
        elif score > 30:
            risk = 'SUPHELII'; aciklama = 'Gorsel icerigi suphe uyandiriyor veya dogrulanamıyor.'
        else:
            risk = 'TEHLIKELI (YANLIS BILGI)'; aciklama = 'Gorsel buyuk ihtimalle yanlis bilgi veya dezenformasyon iceriyor.'

        display_text = (okunan[:200] + '...') if len(okunan) > 200 else okunan
        return score, risk, aciklama, ai_detay, display_text
    except Exception as e:
        print("[GORSEL HATA]: " + str(e))
        return 50, 'ANALIZ HATASI', str(e), '', ''

# ─────────────────────────────────────────────────────────────────
# 6. HTML TEMPLATE
# ─────────────────────────────────────────────────────────────────
HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>DEFANS PRO AI — Dezenformasyon Tespit Sistemi</title>
<meta name="description" content="Google Gemini yapay zekası ile yalan haber ve dezenformasyon tespiti yapın.">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800&display=swap" rel="stylesheet">
<style>
:root {
  --bg: #070b14;
  --surface: rgba(255,255,255,0.04);
  --border: rgba(255,255,255,0.08);
  --accent: #3b82f6;
  --accent2: #8b5cf6;
  --green: #10b981;
  --yellow: #f59e0b;
  --orange: #f97316;
  --red: #ef4444;
  --text: #e2e8f0;
  --muted: #64748b;
}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Inter',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow-x:hidden}

/* Animated BG */
body::before{
  content:'';position:fixed;inset:0;z-index:-1;
  background:
    radial-gradient(ellipse 80% 50% at 20% 10%, rgba(59,130,246,0.12) 0%, transparent 60%),
    radial-gradient(ellipse 60% 40% at 80% 80%, rgba(139,92,246,0.1) 0%, transparent 60%);
  animation:bgPulse 8s ease-in-out infinite alternate;
}
@keyframes bgPulse{from{opacity:0.6}to{opacity:1}}

/* HEADER */
header{
  border-bottom:1px solid var(--border);
  backdrop-filter:blur(20px);
  background:rgba(7,11,20,0.8);
  position:sticky;top:0;z-index:100;
}
.header-inner{
  max-width:1100px;margin:auto;
  display:flex;align-items:center;justify-content:space-between;
  padding:16px 24px;
}
.logo{display:flex;align-items:center;gap:10px}
.logo-icon{
  width:38px;height:38px;border-radius:10px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  display:flex;align-items:center;justify-content:center;font-size:20px;
}
.logo-text{font-size:18px;font-weight:700;letter-spacing:-0.3px}
.logo-text span{color:var(--accent)}
.badge{
  background:rgba(59,130,246,0.15);border:1px solid rgba(59,130,246,0.3);
  color:var(--accent);padding:4px 12px;border-radius:20px;font-size:12px;font-weight:600;
}

/* MAIN LAYOUT */
.container{max-width:1100px;margin:auto;padding:40px 24px}
.hero{text-align:center;margin-bottom:48px}
.hero h1{
  font-size:clamp(32px,5vw,54px);font-weight:800;
  letter-spacing:-1px;line-height:1.15;margin-bottom:16px;
  background:linear-gradient(135deg,#fff 30%,#94a3b8);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;
}
.hero p{color:var(--muted);font-size:17px;max-width:520px;margin:auto;line-height:1.6}

/* STATS BAR */
.stats{display:flex;gap:16px;justify-content:center;margin-bottom:48px;flex-wrap:wrap}
.stat{
  background:var(--surface);border:1px solid var(--border);
  border-radius:14px;padding:16px 28px;text-align:center;
}
.stat-num{font-size:26px;font-weight:700;color:#fff}
.stat-label{font-size:12px;color:var(--muted);margin-top:2px}

/* GRID */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:start}
@media(max-width:768px){.grid{grid-template-columns:1fr}}

/* CARD */
.card{
  background:var(--surface);
  border:1px solid var(--border);
  border-radius:20px;padding:28px;
  backdrop-filter:blur(10px);
}
.card-title{font-size:15px;font-weight:600;color:var(--muted);margin-bottom:20px;text-transform:uppercase;letter-spacing:0.5px}

/* FORM */
label{display:block;font-size:13px;color:var(--muted);margin-bottom:6px;font-weight:500}
input,textarea{
  width:100%;background:rgba(255,255,255,0.05);
  border:1px solid var(--border);border-radius:12px;
  padding:13px 16px;color:#fff;font-size:15px;font-family:'Inter',sans-serif;
  transition:border-color 0.2s,background 0.2s;resize:vertical;
}
input:focus,textarea:focus{
  outline:none;border-color:var(--accent);
  background:rgba(59,130,246,0.07);
}
textarea{min-height:140px}
.form-group{margin-bottom:18px}

.btn{
  width:100%;padding:15px;border:none;border-radius:14px;
  background:linear-gradient(135deg,var(--accent),var(--accent2));
  color:#fff;font-size:15px;font-weight:700;cursor:pointer;
  letter-spacing:0.3px;transition:opacity 0.2s,transform 0.15s;
  display:flex;align-items:center;justify-content:center;gap:8px;
}
.btn:hover{opacity:0.88;transform:translateY(-1px)}
.btn:active{transform:translateY(0)}
.btn.loading{opacity:0.7;pointer-events:none}

/* RESULT CARD */
.result-area{display:none;margin-top:20px}
.result-area.show{display:block;animation:fadeIn 0.4s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}

.meter-wrap{margin:16px 0}
.meter-label{display:flex;justify-content:space-between;font-size:13px;margin-bottom:8px}
.meter-track{background:rgba(255,255,255,0.08);border-radius:100px;height:12px;overflow:hidden}
.meter-fill{
  height:12px;border-radius:100px;
  transition:width 1s cubic-bezier(.4,0,.2,1);
  background:linear-gradient(90deg,#ef4444,#f59e0b,#10b981);
}

.risk-badge{
  display:inline-flex;align-items:center;gap:6px;
  padding:6px 16px;border-radius:100px;font-weight:700;font-size:13px;
}
.risk-guvenli{background:rgba(16,185,129,0.15);color:#10b981;border:1px solid rgba(16,185,129,0.3)}
.risk-muhtemelen{background:rgba(59,130,246,0.15);color:#60a5fa;border:1px solid rgba(59,130,246,0.3)}
.risk-suphe{background:rgba(245,158,11,0.15);color:#f59e0b;border:1px solid rgba(245,158,11,0.3)}
.risk-tehlike{background:rgba(239,68,68,0.15);color:#ef4444;border:1px solid rgba(239,68,68,0.3)}
.risk-hata{background:rgba(100,116,139,0.15);color:#94a3b8;border:1px solid rgba(100,116,139,0.2)}

.detail-box{
  background:rgba(255,255,255,0.03);border:1px solid var(--border);
  border-radius:12px;padding:14px;margin-top:14px;
  font-size:14px;line-height:1.65;color:#cbd5e1;
}

/* TABLE */
.table-wrap{overflow-x:auto;margin-top:8px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{
  padding:10px 14px;text-align:left;
  color:var(--muted);font-weight:600;font-size:11px;
  text-transform:uppercase;letter-spacing:0.5px;
  border-bottom:1px solid var(--border);
}
td{padding:12px 14px;border-bottom:1px solid rgba(255,255,255,0.04);vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(255,255,255,0.02)}

.mini-bar{
  display:flex;align-items:center;gap:8px;
}
.mini-track{
  flex:1;background:rgba(255,255,255,0.08);
  border-radius:100px;height:6px;overflow:hidden;min-width:60px;
}
.mini-fill{
  height:6px;border-radius:100px;
  background:linear-gradient(90deg,#ef4444,#f59e0b,#10b981);
}
.score-num{font-weight:700;color:#fff;min-width:32px;text-align:right;font-size:13px}

.empty-state{text-align:center;padding:40px;color:var(--muted);font-size:14px}

/* SPINNER */
.spinner{
  width:18px;height:18px;border:2px solid rgba(255,255,255,0.3);
  border-top-color:#fff;border-radius:50%;
  animation:spin 0.7s linear infinite;display:none;
}
.btn.loading .spinner{display:block}
.btn.loading .btn-text{display:none}
@keyframes spin{to{transform:rotate(360deg)}}

/* TABS */
.tabs{display:flex;gap:4px;background:rgba(255,255,255,0.05);border-radius:12px;padding:4px;margin-bottom:20px}
.tab{
  flex:1;padding:9px 0;text-align:center;font-size:13px;font-weight:600;
  border-radius:9px;cursor:pointer;color:var(--muted);
  transition:all 0.2s;border:none;background:none;
}
.tab.active{background:rgba(255,255,255,0.1);color:#fff}
.tab-panel{display:none}
.tab-panel.active{display:block}
/* DROP ZONE */
.dropzone{
  border:2px dashed var(--border);border-radius:14px;
  padding:32px;text-align:center;cursor:pointer;
  transition:border-color 0.2s,background 0.2s;
  color:var(--muted);font-size:14px;
}
.dropzone:hover,.dropzone.drag{border-color:var(--accent);background:rgba(59,130,246,0.05);color:#fff}
.dropzone input{display:none}
.dropzone .dz-icon{font-size:32px;margin-bottom:8px}
.dz-filename{margin-top:8px;font-size:12px;color:var(--accent);word-break:break-all}
/* FILE PREVIEW */
#imgPreview{max-width:100%;border-radius:10px;margin-top:12px;display:none}

/* MODAL */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.8); backdrop-filter: blur(5px);
  display: none; align-items: center; justify-content: center; z-index: 1000;
  opacity: 0; transition: opacity 0.3s; padding: 20px;
}
.modal-overlay.show { display: flex; opacity: 1; }
.modal {
  background: var(--bg); border: 1px solid var(--border); border-radius: 20px;
  padding: 30px; max-width: 650px; width: 100%; max-height: 85vh; overflow-y: auto;
  transform: translateY(20px); transition: transform 0.3s;
  position: relative; box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
}
.modal-overlay.show .modal { transform: translateY(0); }
.modal-close {
  position: absolute; top: 20px; right: 20px; background: none; border: none;
  color: var(--muted); font-size: 28px; cursor: pointer; transition: color 0.2s;
  line-height: 1;
}
.modal-close:hover { color: #fff; }
.modal-title { font-size: 18px; font-weight: 700; color: #fff; margin-bottom: 20px; padding-right: 30px; display: flex; align-items: center; gap: 10px;}
.modal-section-title { font-size: 12px; color: var(--muted); margin-bottom: 8px; font-weight: 600; letter-spacing: 0.5px; text-transform: uppercase; }
.modal-content-text { font-size: 14px; color: #e2e8f0; background: var(--surface); padding: 18px; border-radius: 12px; margin-bottom: 24px; word-break: break-word; line-height: 1.6; border: 1px solid var(--border); }
.modal-detail-text { font-size: 15px; line-height: 1.6; color: #cbd5e1; border-left: 4px solid var(--accent); background: rgba(59,130,246,0.05); padding: 18px; border-radius: 0 12px 12px 0; }
.report-row { cursor: pointer; transition: background 0.2s; }
.report-row:hover { background: rgba(255,255,255,0.05); }
</style>
</head>
<body>

<header>
  <div class="header-inner">
    <div class="logo">
      <div class="logo-icon">🛡️</div>
      <div class="logo-text">DEFANS <span>PRO</span></div>
    </div>
    <span class="badge">AI Powered</span>
  </div>
</header>

<div class="container">

  <div class="hero reveal">
    <h1>Dezenformasyona Karşı<br>Yapay Zeka Kalkanı</h1>
    <p>Google Gemini AI ile haber ve içerikleri gerçek zamanlı analiz edin. Yalan haberleri, manipülasyonu ve dezenformasyonu anında tespit edin.</p>
  </div>

  <div class="stats reveal">
    <div class="stat">
      <div class="stat-num" id="totalCount">{{ total }}</div>
      <div class="stat-label">Toplam Analiz</div>
    </div>
    <div class="stat">
      <div class="stat-num" id="dangerCount" style="color:var(--red)">{{ tehlikeli }}</div>
      <div class="stat-label">Tehlikeli İçerik</div>
    </div>
    <div class="stat">
      <div class="stat-num" id="safeCount" style="color:var(--green)">{{ guvenli }}</div>
      <div class="stat-label">Güvenli İçerik</div>
    </div>
  </div>

  <div class="grid">

    <!-- FORM KART -->
    <div class="card reveal">
      <div class="card-title">🔍 Yeni Analiz</div>

      <!-- SEKMELER -->
      <div class="tabs">
        <button class="tab active" data-tab="metin" type="button">📝 Metin</button>
        <button class="tab" data-tab="url" type="button">🔗 URL</button>
        <button class="tab" data-tab="gorsel" type="button">🖼️ Görsel</button>
      </div>

      <form id="analyzeForm" enctype="multipart/form-data">
        <input type="hidden" name="input_type" id="inputTypeField" value="metin">
        <div class="form-group">
          <label for="email">E-posta Adresiniz</label>
          <input id="email" name="email" type="email" placeholder="ornek@gmail.com" required>
        </div>

        <!-- METİN PANELİ -->
        <div class="tab-panel active" id="panel-metin">
          <div class="form-group">
            <label for="content">Analiz Edilecek Metin</label>
            <textarea id="content" name="content" placeholder="Doğruluğundan emin olmadığınız haber veya iddiayı yapıştırın..."></textarea>
          </div>
        </div>

        <!-- URL PANELİ -->
        <div class="tab-panel" id="panel-url">
          <div class="form-group">
            <label for="urlInput">Haber veya Twitter/X Bağlantısı</label>
            <input id="urlInput" name="url" type="url" placeholder="https://twitter.com/... veya https://www.haberler.com/...">
            <p style="font-size:12px;color:var(--muted);margin-top:6px">Twitter/X, haber siteleri ve blog linkleri desteklenir.</p>
          </div>
        </div>

        <!-- GÖRSEL PANELİ -->
        <div class="tab-panel" id="panel-gorsel">
          <div class="form-group">
            <label>Fotoğraf Yükle (JPG, PNG, WEBP)</label>
            <div class="dropzone" id="dropzone">
              <input type="file" name="image" id="imageInput" accept="image/*">
              <div class="dz-icon">📷</div>
              <div>Fotoğrafı buraya sürükleyin veya tıklayın</div>
              <div class="dz-filename" id="dzFilename"></div>
            </div>
            <img id="imgPreview" alt="Secilen gorsel">
          </div>
        </div>

        <button type="submit" class="btn" id="submitBtn">
          <div class="spinner"></div>
          <span class="btn-text">🔍 Yapay Zeka Analizi Başlat</span>
        </button>
      </form>

      <div class="result-area" id="resultArea">
        <div class="meter-wrap">
          <div class="meter-label">
            <span>Güven Puanı</span>
            <span id="scoreText">—</span>
          </div>
          <div class="meter-track">
            <div class="meter-fill" id="meterFill" style="width:0%"></div>
          </div>
        </div>
        <div id="riskBadge"></div>
        <div class="detail-box" id="detailBox"></div>
      </div>
    </div>

    <!-- SON ANALİZLER -->
    <div class="card reveal">
      <div class="card-title">📊 Son Analizler</div>
      {% if reports %}
      <div class="table-wrap">
        <table>
          <tr>
            <th>İçerik</th>
            <th>Puan</th>
            <th>Durum</th>
          </tr>
          {% for r in reports %}
          <tr class="report-row" data-content="{{ r['content']|e }}" data-score="{{ r['score'] }}" data-risk="{{ r['risk']|e }}" data-detail="{{ r['ai_detay']|e if r['ai_detay'] else r['aciklama']|e }}">
            <td title="{{ r['content'] }}" style="max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:#cbd5e1">
              {{ r['content'][:45] }}{% if r['content']|length > 45 %}…{% endif %}
            </td>
            <td>
              <div class="mini-bar">
                <div class="mini-track">
                  <div class="mini-fill" style="width:{{ r['score'] }}%"></div>
                </div>
                <span class="score-num">{{ r['score'] }}</span>
              </div>
            </td>
            <td>
              {% if 'GÜVENLİ' == r['risk'] %}
                <span class="risk-badge risk-guvenli">✅ Güvenli</span>
              {% elif 'MUHTEMELEN' in r['risk'] %}
                <span class="risk-badge risk-muhtemelen">🔵 Muhtemel</span>
              {% elif 'ŞÜPHELİ' == r['risk'] %}
                <span class="risk-badge risk-suphe">⚠️ Şüpheli</span>
              {% elif 'TEHLİKELİ' in r['risk'] %}
                <span class="risk-badge risk-tehlike">🚨 Tehlikeli</span>
              {% else %}
                <span class="risk-badge risk-hata">❓ {{ r['risk'] }}</span>
              {% endif %}
            </td>
          </tr>
          {% endfor %}
        </table>
      </div>
      {% else %}
      <div class="empty-state">Henüz analiz yapılmadı.<br>İlk analizinizi soldaki formdan başlatın.</div>
      {% endif %}
    </div>

  </div>
</div>

<!-- MODAL HTML -->
<div class="modal-overlay" id="detailModal">
  <div class="modal">
    <button class="modal-close" id="closeModal">&times;</button>
    <div class="modal-title">
      <span id="modalRiskBadge"></span>
      <span style="color:var(--muted); font-weight:400; font-size:14px;">Güven Puanı: <b id="modalScore" style="color:#fff; font-size:16px;"></b>/100</span>
    </div>
    <div class="modal-section-title">İncelenen İçerik</div>
    <div class="modal-content-text" id="modalContentText"></div>
    <div class="modal-section-title">Yapay Zeka Analiz Detayı</div>
    <div class="modal-detail-text" id="modalDetailText"></div>
  </div>
</div>

<script>
// SEKMELER
const tabs = document.querySelectorAll('.tab');
tabs.forEach(tab => {
  tab.addEventListener('click', () => {
    tabs.forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    document.getElementById('panel-' + tab.dataset.tab).classList.add('active');
    document.getElementById('inputTypeField').value = tab.dataset.tab;
  });
});
// GORSEL DROP ZONE
const dz = document.getElementById('dropzone');
const imgInput = document.getElementById('imageInput');
const imgPreview = document.getElementById('imgPreview');
dz.addEventListener('click', () => imgInput.click());
imgInput.addEventListener('change', () => showPreview(imgInput.files[0]));
dz.addEventListener('dragover', e => { e.preventDefault(); dz.classList.add('drag'); });
dz.addEventListener('dragleave', () => dz.classList.remove('drag'));
dz.addEventListener('drop', e => {
  e.preventDefault(); dz.classList.remove('drag');
  const file = e.dataTransfer.files[0];
  if (file && file.type.startsWith('image/')) {
    const dt = new DataTransfer(); dt.items.add(file);
    imgInput.files = dt.files; showPreview(file);
  }
});
function showPreview(file) {
  if (!file) return;
  document.getElementById('dzFilename').textContent = file.name;
  const reader = new FileReader();
  reader.onload = ev => { imgPreview.src = ev.target.result; imgPreview.style.display = 'block'; };
  reader.readAsDataURL(file);
}
// SCROLL REVEAL
const revealEls = document.querySelectorAll('.reveal');
const io = new IntersectionObserver(entries => {
  entries.forEach(e => { if(e.isIntersecting) { e.target.classList.add('visible'); io.unobserve(e.target); } });
}, { threshold: 0.1 });
revealEls.forEach(el => io.observe(el));

// MODAL LOGIC
const modal = document.getElementById('detailModal');
const closeModal = document.getElementById('closeModal');

document.querySelectorAll('.report-row').forEach(row => {
  row.addEventListener('click', () => {
    const content = row.dataset.content;
    const score = row.dataset.score;
    const risk = row.dataset.risk;
    const detail = row.dataset.detail;
    
    document.getElementById('modalContentText').textContent = content;
    document.getElementById('modalScore').textContent = score;
    document.getElementById('modalDetailText').textContent = detail;
    
    let cls = 'risk-hata', label = risk;
    const rUpper = (risk || '').toUpperCase();
    if (rUpper.includes('GUVEN')) { cls='risk-guvenli'; label='✅ Güvenli'; }
    else if (rUpper.includes('MUHTEMEL')) { cls='risk-muhtemelen'; label='🔵 Muhtemelen Doğru'; }
    else if (rUpper.includes('SUPHE') || rUpper.includes('SOUPHE')) { cls='risk-suphe'; label='⚠️ Şüpheli'; }
    else if (rUpper.includes('TEHLIKE')) { cls='risk-tehlike'; label='🚨 Tehlikeli'; }
    else if (rUpper.includes('HATA')) { cls='risk-hata'; label='❓ Analiz Hatasi'; }
    
    document.getElementById('modalRiskBadge').innerHTML = '<span class="risk-badge ' + cls + '">' + label + '</span>';
    modal.classList.add('show');
  });
});

closeModal.addEventListener('click', () => modal.classList.remove('show'));
modal.addEventListener('click', e => {
  if(e.target === modal) modal.classList.remove('show');
});

// FORM SUBMIT
document.getElementById('analyzeForm').addEventListener('submit', async function(e) {
  e.preventDefault();
  const btn = document.getElementById('submitBtn');
  btn.classList.add('loading');
  const fd = new FormData(this);
  try {
    const res = await fetch('/analiz', { method:'POST', body: fd });
    const data = await res.json();
    if (data.error) { alert('Hata: ' + data.error); return; }
    const score = data.score;
    document.getElementById('scoreText').textContent = score + '/100';
    document.getElementById('meterFill').style.width = score + '%';
    const riskMap = {
      'GUVENLI':['risk-guvenli','OK GUVENLI'],
      'MUHTEMELEN':['risk-muhtemelen','Muhtemelen Dogru'],
      'SUPHELII':['risk-suphe','Supheli'],
      'SOUPHE':['risk-suphe','Supheli'],
      'TEHLIKELI':['risk-tehlike','TEHLIKELI YANLIS BILGI'],
      'ANALIZ HATASI':['risk-hata','Analiz Hatasi'],
    };
    let cls = 'risk-hata', label = data.risk;
    const rUpper = (data.risk || '').toUpperCase();
    if (rUpper.includes('GUVEN')) { cls='risk-guvenli'; label='Guvenli'; }
    else if (rUpper.includes('MUHTEMEL')) { cls='risk-muhtemelen'; label='Muhtemelen Dogru'; }
    else if (rUpper.includes('SUPHE') || rUpper.includes('SOUPHE')) { cls='risk-suphe'; label='Supheli'; }
    else if (rUpper.includes('TEHLIKE')) { cls='risk-tehlike'; label='TEHLIKELI - YANLIS BILGI'; }
    else if (rUpper.includes('HATA')) { cls='risk-hata'; label='Analiz Hatasi'; }
    document.getElementById('riskBadge').innerHTML = '<span class="risk-badge ' + cls + '">' + label + '</span>';
    document.getElementById('detailBox').textContent = data.ai_detay || data.aciklama;
    document.getElementById('resultArea').classList.add('show');
    document.getElementById('totalCount').textContent = data.stats.total;
    document.getElementById('dangerCount').textContent = data.stats.tehlikeli;
    document.getElementById('safeCount').textContent = data.stats.guvenli;
    setTimeout(() => location.reload(), 2000);
  } catch(err) {
    alert('Bir hata olustu: ' + err);
  } finally {
    btn.classList.remove('loading');
  }
});
</script>


</body>
</html>"""

# ─────────────────────────────────────────────────────────────────
# 6. ROUTES
# ─────────────────────────────────────────────────────────────────
def get_stats(conn):
    rows = conn.execute("SELECT risk FROM reports").fetchall()
    total     = len(rows)
    tehlikeli = sum(1 for r in rows if 'TEHLİKELİ' in (r['risk'] or ''))
    guvenli   = sum(1 for r in rows if r['risk'] in ('GÜVENLİ', 'MUHTEMELEN DOĞRU'))
    return total, tehlikeli, guvenli

@app.route('/')
def home():
    conn = get_db()
    reports = conn.execute("SELECT * FROM reports ORDER BY id DESC LIMIT 15").fetchall()
    total, tehlikeli, guvenli = get_stats(conn)
    conn.close()
    return render_template_string(HTML, reports=reports,
                                  total=total, tehlikeli=tehlikeli, guvenli=guvenli)

@app.route('/analiz', methods=['POST'])
def analiz():
    email      = request.form.get('email', '').strip()
    input_type = request.form.get('input_type', 'text')
    content_label = ''

    if input_type == 'gorsel':
        # ── GÖRSEL ANALİZİ ──
        if 'image' not in request.files or request.files['image'].filename == '':
            return jsonify({'error': 'Gorsel secilmedi'}), 400
        f = request.files['image']
        image_bytes = f.read()
        mime_type   = f.mimetype or 'image/jpeg'
        score, risk, aciklama, ai_detay, okunan = gorsel_analiz_et(image_bytes, mime_type)
        content = f'[GORSEL] {okunan}' if okunan else '[GORSEL - metin okunamadi]'
        content_label = f.filename

    elif input_type == 'url':
        # ── URL ANALİZİ ──
        url = request.form.get('url', '').strip()
        if not url:
            return jsonify({'error': 'URL bos'}), 400
        metin, kaynak = url_icerik_cek(url)
        if not metin:
            return jsonify({'error': 'URL icerik cekiLemedi: ' + kaynak}), 400
        score, risk, aciklama, ai_detay = analiz_et_ai(metin)
        content = metin[:500]
        content_label = url

    else:
        # ── METİN ANALİZİ ──
        content = request.form.get('content', '').strip()
        if not content:
            return jsonify({'error': 'Icerik bos'}), 400
        score, risk, aciklama, ai_detay = analiz_et_ai(content)
        content_label = content[:80]

    h = hashlib.sha256(content.encode()).hexdigest()
    conn = get_db()
    conn.execute(
        'INSERT INTO reports (content, email, status, score, risk, hash, aciklama, ai_detay, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?)',
        (content_label or content, email, 'AI Analiz Edildi', score, risk, h,
         aciklama, ai_detay, datetime.now().strftime('%d.%m.%Y %H:%M'))
    )
    conn.commit()
    total, tehlikeli, guvenli = get_stats(conn)
    conn.close()
    mail_gonder(content_label or content, score, risk, aciklama, email)
    return jsonify({
        'score': score, 'risk': risk, 'aciklama': aciklama, 'ai_detay': ai_detay,
        'stats': {'total': total, 'tehlikeli': tehlikeli, 'guvenli': guvenli}
    })

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Sunucu baslatiliyor --> http://127.0.0.1:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
