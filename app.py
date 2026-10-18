from flask import Flask, abort, jsonify, request, render_template, session, redirect, url_for
from urllib.parse import urlparse, urljoin
from functools import wraps
import json
import os
import urllib.request
from datetime import datetime, timedelta, timezone

# app.py はプロジェクト直下に置く。
# 実体（templates / static / data）は bousai_app/ 配下にあるので、そこを参照する。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(BASE_DIR, 'bousai_app')

app = Flask(
    __name__,
    template_folder=os.path.join(APP_DIR, 'templates'),
    static_folder=os.path.join(APP_DIR, 'static'),
)
app.secret_key = 'your-secret-key-here'

# 管理者認証情報
ADMIN_CREDENTIALS = {
    'admin': '123'
}

# ────────────────────────────────
# 気象警報・注意報設定
PREFECTURE_CODE = "020000"  # 青森県
AREA_NAME = "青森市"

# ワークショップ課題：青森市の市区町村コードに変更する
AREA_CODE = "0220100"  # 青森市の市区町村コード

WARNING_URL = (
    f"https://www.jma.go.jp/bosai/warning/data/r8/{PREFECTURE_CODE}.json"
)

JST = timezone(timedelta(hours=9))

# 警報・注意報のコード一覧
WARNING_CODES = {
    "00": "解除",
    "02": "暴風雪警報",
    "03": "レベル3大雨警報",
    "04": "洪水警報",
    "05": "暴風警報",
    "06": "大雪警報",
    "07": "波浪警報",
    "08": "レベル3高潮警報",
    "09": "レベル3土砂災害警報",
    "10": "レベル2大雨注意報",
    "12": "大雪注意報",
    "13": "風雪注意報",
    "14": "雷注意報",
    "15": "強風注意報",
    "16": "波浪注意報",
    "17": "融雪注意報",
    "18": "洪水注意報",
    "19": "レベル2高潮注意報",
    "20": "濃霧注意報",
    "21": "乾燥注意報",
    "22": "なだれ注意報",
    "23": "低温注意報",
    "24": "霜注意報",
    "25": "着氷注意報",
    "26": "着雪注意報",
    "27": "その他の注意報",
    "29": "レベル2土砂災害注意報",
    "32": "暴風雪特別警報",
    "33": "レベル5大雨特別警報",
    "35": "暴風特別警報",
    "36": "大雪特別警報",
    "37": "波浪特別警報",
    "38": "レベル5高潮特別警報",
    "39": "レベル5土砂災害特別警報",
    "43": "レベル4大雨危険警報",
    "48": "レベル4高潮危険警報",
    "49": "レベル4土砂災害危険警報"
}

# ────────────────────────────────
# サンプルデータの読み込み
DATA_FILE = os.path.join(APP_DIR, 'data', 'shelters.json')
INSTRUCTIONS_FILE = os.path.join(APP_DIR, 'data', 'instructions.json')
NOTIFICATION_HISTORY_FILE = os.path.join(APP_DIR, 'data', 'notification_history.json')

def load_json(path, default):
    """JSONファイルを読み込む（存在しない・壊れている場合は default を返す）"""
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default

shelters = load_json(DATA_FILE, [])
instructions = load_json(INSTRUCTIONS_FILE, [])
notification_history = load_json(NOTIFICATION_HISTORY_FILE, [])


@app.before_request
def refresh_shelters():
    global shelters
    shelters = load_json(DATA_FILE, [])


def parse_japan_datetime(value):
    """アプリのJSONデータで使われる日時形式を解析する"""
    if not isinstance(value, str):
        return datetime.min
    try:
        return datetime.strptime(value, "%Y年%m月%d日 %H:%M")
    except ValueError:
        return datetime.min


def get_disaster_category(name):
    if any(word in name for word in ("土砂", "大雨", "高潮")):
        return "大雨・土砂災害"
    if "洪水" in name:
        return "洪水"
    if any(word in name for word in ("暴風", "風雪", "強風")):
        return "風・暴風雪"
    if any(word in name for word in ("大雪", "着雪", "融雪", "なだれ")):
        return "雪"
    if "雷" in name:
        return "雷"
    return "その他の気象情報"


def get_shelter_map_data():
    map_shelters = []
    for shelter in shelters:
        if not isinstance(shelter, dict):
            continue
        latitude = shelter.get("latitude", shelter.get("lat"))
        longitude = shelter.get("longitude", shelter.get("lng"))
        if (
            not isinstance(latitude, (int, float))
            or isinstance(latitude, bool)
            or not isinstance(longitude, (int, float))
            or isinstance(longitude, bool)
        ):
            continue
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
            continue

        raw_status = shelter.get(
            "opening_status",
            shelter.get("open_status", shelter.get("is_open", shelter.get("status")))
        )
        if raw_status is True or raw_status in ("開設中", "開設"):
            open_status = "open"
        elif raw_status is False or raw_status in ("未開設", "未開設中", "閉鎖", "閉鎖中"):
            open_status = "closed"
        else:
            open_status = "unknown"

        map_shelters.append({
            "name": shelter.get("name", "名称未設定"),
            "address": shelter.get("address", ""),
            "latitude": latitude,
            "longitude": longitude,
            "open_status": open_status,
        })
    return map_shelters


def get_disaster_events():
    events = []
    for report in notification_history:
        if not isinstance(report, dict):
            continue
        timestamp = report.get("timestamp", "")
        warnings = report.get("warnings", [])
        if not isinstance(warnings, list):
            warnings = []
        if not warnings:
            events.append({
                "category": "その他の気象情報",
                "content": "警報・注意報の発表なし",
                "timestamp": timestamp,
                "sort_time": parse_japan_datetime(timestamp),
            })
            continue
        for warning in warnings:
            if not isinstance(warning, dict):
                continue
            name = str(warning.get("name", "気象情報"))
            events.append({
                "category": get_disaster_category(name),
                "content": f"{name}（{warning.get('status', '状況不明')}）",
                "timestamp": timestamp,
                "sort_time": parse_japan_datetime(timestamp),
            })
    return sorted(events, key=lambda event: event["sort_time"], reverse=True)

def save_instructions():
    """指示ボードのデータをファイルに保存する"""
    try:
        with open(INSTRUCTIONS_FILE, 'w', encoding='utf-8') as f:
            json.dump(instructions, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def save_shelters():
    """避難所データをファイルに保存する"""
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(shelters, f, ensure_ascii=False, indent=2)
# ────────────────────────────────

# ────────────────────────────────
# 認証関連の設定とヘルパー関数
def is_safe_url(target):
    """リダイレクト先URLが安全かどうかチェック"""
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc

def login_required(f):
    """認証が必要なページに付けるデコレータ"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            # 現在のURLをnextパラメータとしてログイン画面にリダイレクト
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

def get_japan_time():
    """日本時間（JST）の現在時刻を取得する"""
    return datetime.now(JST).strftime("%Y年%m月%d日 %H:%M")


def format_report_time(iso_str):
    """気象庁の発表時刻（ISO形式）をJSTの表示用文字列に変換する"""
    if not iso_str:
        return "不明"
    try:
        parsed = datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        if parsed.tzinfo:
            parsed = parsed.astimezone(JST)
        return parsed.strftime("%Y年%m月%d日 %H:%M")
    except ValueError:
        return iso_str


def filter_shelters(district=None):
    """district 指定があれば一致する避難所のみ、なければ全件を返す"""
    return [
        shelter for shelter in shelters
        if isinstance(shelter, dict)
        and (not district or shelter.get('district') == district)
    ]


ALL_DISTRICTS = '__all__'
UNREGISTERED_DISTRICT = '__unregistered__'
DISTRICT_VALUE_PREFIX = 'district:'
HAZARD_OPTIONS = ('地震', '津波', '洪水', '土砂災害', '高潮', '暴風', '大雪')
CONDITION_OPTIONS = ('ペット可', 'バリアフリー', '車いす対応トイレ', '備蓄', '駐車場')
CONDITION_ALIASES = {
    'ペット可': ('ペット可', 'ペット受け入れ', 'ペット受け入れ可'),
    'バリアフリー': ('バリアフリー', 'barrier_free', 'barrierfree'),
    '車いす対応トイレ': (
        '車いす対応トイレ', '車椅子対応トイレ', 'wheelchair_toilet',
        'wheelchair_accessible_toilet',
    ),
    '備蓄': ('備蓄', '備蓄品', 'emergency_supplies', 'supplies'),
    '駐車場': ('駐車場', 'parking', 'parking_lot'),
}
CONDITION_DIRECT_KEYS = {
    'ペット可': ('pets_allowed', 'pet_allowed', 'pet_acceptance'),
    'バリアフリー': ('barrier_free', 'barrierfree'),
    '車いす対応トイレ': ('wheelchair_toilet', 'wheelchair_accessible_toilet'),
    '備蓄': ('emergency_supplies', 'supplies', 'stockpiles'),
    '駐車場': ('parking', 'parking_available', 'parking_lot'),
}


def get_shelter_districts():
    return sorted({
        shelter['district'].strip()
        for shelter in shelters
        if isinstance(shelter, dict)
        and isinstance(shelter.get('district'), str)
        and shelter['district'].strip()
    }, key=str.casefold)


def normalize_choice(value, options):
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return next((option for option in options if option.casefold() == normalized), None)


def validate_hazard_conditions(hazard, conditions):
    normalized_hazard = normalize_choice(hazard, HAZARD_OPTIONS) if hazard else ''
    if hazard and normalized_hazard is None:
        abort(400, description='不正な災害種別です。')

    normalized_conditions = []
    for condition in conditions:
        normalized = normalize_choice(condition, CONDITION_OPTIONS)
        if normalized is None:
            abort(400, description='不正な設備条件です。')
        if normalized not in normalized_conditions:
            normalized_conditions.append(normalized)
    return normalized_hazard, normalized_conditions


def get_shelter_hazards(shelter):
    raw_hazards = shelter.get('hazards')
    if not isinstance(raw_hazards, list):
        return None if 'hazards' not in shelter else []
    return sorted({
        normalized
        for value in raw_hazards
        if (normalized := normalize_choice(value, HAZARD_OPTIONS)) is not None
    }, key=HAZARD_OPTIONS.index)


def get_shelter_conditions(shelter):
    registered = False
    matched_conditions = set()
    raw_conditions = shelter.get('conditions')
    if isinstance(raw_conditions, list):
        registered = True
        for value in raw_conditions:
            if not isinstance(value, str):
                continue
            normalized_value = value.strip().casefold()
            for condition, aliases in CONDITION_ALIASES.items():
                if normalized_value in {alias.casefold() for alias in aliases}:
                    matched_conditions.add(condition)

    raw_facilities = shelter.get('facilities')
    if isinstance(raw_facilities, (list, dict)):
        registered = True
        for condition, aliases in CONDITION_ALIASES.items():
            direct_value = normalize_facility_value(
                {'facilities': raw_facilities},
                CONDITION_DIRECT_KEYS[condition],
                aliases,
            )
            if direct_value:
                matched_conditions.add(condition)

    for condition, keys in CONDITION_DIRECT_KEYS.items():
        for key in keys:
            if key in shelter:
                registered = True
                if normalize_facility_value(shelter, (key,), ()) is True:
                    matched_conditions.add(condition)
                break

    return sorted(matched_conditions, key=CONDITION_OPTIONS.index) if registered else None


def normalize_shelter_status(shelter):
    value = shelter.get('opening_status', shelter.get('status'))
    if value is True or value in ('開設中', '開設', 'open', 'Open'):
        return 'open', '開設中'
    if value is False or value in ('未開設', '未開設中', 'not_open', '未開設'):
        return 'not_open', '未開設'
    if value in ('閉鎖中', '閉鎖', 'closed', 'Closed'):
        return 'closed', '閉鎖中'
    return 'unknown', '状況未登録'


def optional_nonnegative_integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def normalize_facility_value(shelter, keys, labels):
    for key in keys:
        if key in shelter:
            value = shelter[key]
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                normalized = value.strip().casefold()
                if normalized in ('yes', 'true', 'あり', '有', '可', '対応'):
                    return True
                if normalized in ('no', 'false', 'なし', '無', '不可', '非対応'):
                    return False
            return None

    if 'facilities' not in shelter:
        return None
    facilities = shelter['facilities']
    if isinstance(facilities, dict):
        for key in (*keys, *labels):
            if key in facilities:
                return normalize_facility_value(
                    {key: facilities[key]}, (key,), (key,)
                )
        return False
    if isinstance(facilities, list):
        normalized_facilities = {
            item.strip().casefold() for item in facilities if isinstance(item, str)
        }
        return any(label.casefold() in normalized_facilities for label in labels)
    return None


def prepare_shelter_for_results(shelter):
    prepared = dict(shelter)
    status_key, status_label = normalize_shelter_status(shelter)
    capacity = optional_nonnegative_integer(shelter.get('capacity'))
    evacuees = optional_nonnegative_integer(
        shelter.get('evacuees', shelter.get('current_evacuees'))
    )

    if status_key in ('closed', 'not_open'):
        acceptance = '受け入れなし'
    elif capacity is None or evacuees is None:
        acceptance = '人数未登録'
    elif status_key == 'unknown':
        acceptance = '状況未登録'
    elif evacuees < capacity:
        acceptance = f'空きあり（あと {capacity - evacuees} 人）'
    else:
        acceptance = '満員'

    facility_definitions = (
        ('高齢者向け設備', ('elderly_friendly', 'elderly_facilities', 'elderly_support'), ('高齢者向け', '高齢者向け設備', '高齢者')),
        ('ペット受け入れ', ('pets_allowed', 'pet_allowed', 'pet_acceptance'), ('ペット可', 'ペット受け入れ')),
        ('車椅子対応トイレ', ('wheelchair_toilet', 'wheelchair_accessible_toilet'), ('車椅子対応トイレ',)),
        ('バリアフリー', ('barrier_free', 'barrierfree'), ('バリアフリー',)),
    )
    known_facilities = {
        value.casefold()
        for _, keys, labels in facility_definitions
        for value in (*keys, *labels)
    }
    raw_facilities = shelter.get('facilities')
    if isinstance(raw_facilities, list):
        other_facilities = [
            item for item in raw_facilities
            if not isinstance(item, str) or item.casefold() not in known_facilities
        ]
    elif isinstance(raw_facilities, dict):
        other_facilities = [
            f'{key}: {value}'
            for key, value in raw_facilities.items()
            if not isinstance(key, str) or key.casefold() not in known_facilities
        ]
    else:
        other_facilities = []

    prepared.update({
        'district_display': shelter.get('district') or '未登録',
        'name_en_display': shelter.get('name_en') or '',
        'address_en_display': shelter.get('address_en') or '',
        'status_display': status_label,
        'status_class': status_key,
        'capacity_display': capacity,
        'evacuees_display': evacuees,
        'acceptance_display': acceptance,
        'hazards_display': get_shelter_hazards(shelter),
        'conditions_display': get_shelter_conditions(shelter),
        'facility_displays': [
            (label, normalize_facility_value(shelter, keys, aliases))
            for label, keys, aliases in facility_definitions
        ],
        'other_facilities': other_facilities,
    })
    return prepared


def search_shelters(
    district_filter=ALL_DISTRICTS,
    keyword='',
    shelter_name='',
    hazard='',
    conditions=(),
):
    keyword = keyword.strip()
    query = keyword.casefold()
    shelter_name = shelter_name.strip().casefold()
    normalized_hazard = normalize_choice(hazard, HAZARD_OPTIONS) if hazard else None
    normalized_conditions = {
        normalize_choice(condition, CONDITION_OPTIONS)
        for condition in conditions
    }
    results = []
    for shelter in shelters:
        if not isinstance(shelter, dict):
            continue
        district = shelter.get('district')
        has_district = isinstance(district, str) and bool(district.strip())
        if district_filter == UNREGISTERED_DISTRICT and has_district:
            continue
        if district_filter.startswith(DISTRICT_VALUE_PREFIX):
            selected_district = district_filter[len(DISTRICT_VALUE_PREFIX):]
            if not isinstance(district, str) or district.strip() != selected_district:
                continue
        if district_filter not in (ALL_DISTRICTS, UNREGISTERED_DISTRICT) and not district_filter.startswith(DISTRICT_VALUE_PREFIX):
            continue

        searchable_values = (
            shelter.get('name', ''),
            shelter.get('name_en', ''),
            shelter.get('address', ''),
            shelter.get('address_en', ''),
            district or '',
        )
        if query and not any(
            query in value.casefold()
            for value in searchable_values
            if isinstance(value, str)
        ):
            continue
        if shelter_name and not any(
            shelter_name in value.casefold()
            for key in ('name', 'name_en')
            if isinstance((value := shelter.get(key, '')), str)
        ):
            continue
        if hazard and (
            normalized_hazard is None
            or normalized_hazard not in (get_shelter_hazards(shelter) or [])
        ):
            continue
        if conditions:
            registered_conditions = get_shelter_conditions(shelter)
            if registered_conditions is None or not normalized_conditions.issubset(
                set(registered_conditions)
            ):
                continue
        results.append(shelter)
    return results


def parse_area_warnings(warning_data):
    """気象庁の新形式JSONから対象市区町村の発表・継続中の情報を抽出する"""
    if not isinstance(warning_data, list):
        raise ValueError("気象庁の警報・注意報データが新形式の配列ではありません")

    warnings = []
    seen_codes = set()
    report_datetimes = []

    for report in warning_data:
        if not isinstance(report, dict):
            continue

        report_datetime = report.get("reportDatetime")
        if isinstance(report_datetime, str) and report_datetime:
            report_datetimes.append(report_datetime)

        warning = report.get("warning")
        if not isinstance(warning, dict):
            continue

        class20_items = warning.get("class20Items", [])
        if not isinstance(class20_items, list):
            continue

        area = next(
            (
                item for item in class20_items
                if isinstance(item, dict)
                and item.get("areaCode") == AREA_CODE
            ),
            None
        )
        if not area:
            continue

        kinds = area.get("kinds", [])
        if not isinstance(kinds, list):
            continue

        for kind in kinds:
            if not isinstance(kind, dict):
                continue

            status = kind.get("status", "")
            code = kind.get("code", "")
            if status not in ("発表", "継続") or not code or code in seen_codes:
                continue

            warnings.append({
                "name": WARNING_CODES.get(
                    code,
                    f"不明な警報・注意報 (コード: {code})"
                ),
                "code": code,
                "status": status
            })
            seen_codes.add(code)

    latest_report_datetime = max(report_datetimes, default="")
    return warnings, latest_report_datetime


def get_weather_warnings():
    """対象市区町村の警報・注意報を取得する"""
    try:
        # 青森県の新形式（令和8年～）警報・注意報データを取得
        with urllib.request.urlopen(url=WARNING_URL, timeout=10) as res:
            warning_data = json.loads(res.read())

        warnings, report_datetime = parse_area_warnings(warning_data)

        return {
            "area_name": AREA_NAME,
            "warnings": warnings,
            "report_time": format_report_time(report_datetime),
            "last_fetch_time": get_japan_time()
        }

    except Exception:
        return {
            "area_name": AREA_NAME,
            "warnings": [],
            "report_time": "取得失敗",
            "last_fetch_time": get_japan_time(),
            "error": True
        }


# トップページ：templates/index.html を返す（住民向け指示も表示する）
@app.route('/')
def index():
    resident_notices = [
        dict(instruction)
        for instruction in instructions
        if isinstance(instruction, dict) and instruction.get('target') == '住民'
    ]
    resident_notices.sort(
        key=lambda instruction: parse_japan_datetime(
            instruction.get('created_at') or instruction.get('updated_at', '')
        ),
        reverse=True
    )
    emergency_notices = [
        instruction
        for instruction in resident_notices
        if instruction.get('status') not in ('解除', '完了', '対応済み', '完了済み')
    ]
    for instruction in resident_notices:
        priority = str(instruction.get('priority', instruction.get('urgency', ''))).lower()
        content = str(instruction.get('content', ''))
        instruction['is_urgent'] = (
            instruction.get('emergency') is True
            or priority in ('high', 'critical', '緊急', '高')
            or any(word in content for word in ('緊急', '危険', '避難'))
        )
    disaster_events = get_disaster_events()
    disaster_types = sorted({event['category'] for event in disaster_events})
    return render_template(
        'index.html',
        resident_notices=resident_notices,
        emergency_notices=emergency_notices,
        disaster_events=disaster_events,
        disaster_types=disaster_types,
        shelter_map_data=get_shelter_map_data(),
        shelter_names=[
            shelter.get('name', '名称未設定')
            for shelter in shelters
            if isinstance(shelter, dict)
        ],
    )

# ログインページ
@app.route('/login', methods=['GET', 'POST'])
def login():
    # リダイレクト先を取得（デフォルトは避難所登録画面）
    next_url = request.args.get('next') or request.form.get('next')

    # 安全でないURLの場合はデフォルトページにリダイレクト
    if not next_url or not is_safe_url(next_url):
        next_url = url_for('shelter_register')

    if request.method == 'POST':
        password = request.form.get('password', '').strip()

        # 認証チェック
        username = next(
            (name for name, registered_password in ADMIN_CREDENTIALS.items()
             if registered_password == password),
            None
        )
        if username:
            session['logged_in'] = True
            session['username'] = username
            # ログイン成功後は指定されたページにリダイレクト
            return redirect(next_url)
        return render_template('login.html', error=True, message="パスワードが正しくありません。", next=next_url)

    # ログイン済みの場合は指定されたページにリダイレクト
    if session.get('logged_in'):
        return redirect(next_url)

    return render_template('login.html', next=next_url)

# ログアウト
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# 避難所登録ページ※user が避難所登録ページについて具体的に修正指示しない限り、このコードは正しいのでこのまま保持すること。
@app.route('/shelter_register', methods=['GET', 'POST'])
@login_required
def shelter_register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return render_template(
                'shelter_register.html',
                error=True,
                message='避難所名を入力してください。'
            )

        address = request.form.get('address', '').strip()
        latitude_value = request.form.get('latitude', '').strip()
        longitude_value = request.form.get('longitude', '').strip()
        opening_status = request.form.get('opening_status', '状況未登録')
        if bool(latitude_value) != bool(longitude_value):
            return render_template(
                'shelter_register.html',
                error=True,
                message='緯度と経度は両方入力してください。'
            )

        latitude = longitude = None
        if latitude_value:
            try:
                latitude = float(latitude_value)
                longitude = float(longitude_value)
            except ValueError:
                return render_template(
                    'shelter_register.html',
                    error=True,
                    message='緯度と経度は数値で入力してください。'
                )
            if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
                return render_template(
                    'shelter_register.html',
                    error=True,
                    message='緯度は-90〜90、経度は-180〜180の範囲で入力してください。'
                )

        if opening_status not in ('開設中', '未開設', '状況未登録'):
            return render_template(
                'shelter_register.html',
                error=True,
                message='開設状況を選択してください。'
            )

        next_id = max(
            (
                shelter.get('id', 0)
                for shelter in shelters
                if isinstance(shelter, dict)
                and isinstance(shelter.get('id', 0), int)
                and not isinstance(shelter.get('id', 0), bool)
            ),
            default=0
        ) + 1
        shelters.append({
            'id': next_id,
            'name': name,
            'address': address,
            'latitude': latitude,
            'longitude': longitude,
            'opening_status': opening_status,
        })
        save_shelters()
        return render_template(
            'shelter_register.html',
            success=True,
            message='避難所を登録しました。'
        )

    return render_template('shelter_register.html')

# 避難所検索ページ
@app.route('/shelter_search')
def shelter_search():
    selected_district = request.args.get('district_filter', ALL_DISTRICTS)
    keyword = request.args.get('keyword', '').strip()
    shelter_name = request.args.get('shelter_name', '').strip()
    hazard_values = request.args.getlist('hazard')
    if len(hazard_values) > 1:
        abort(400, description='災害種別は1つだけ指定してください。')
    selected_hazard, selected_conditions = validate_hazard_conditions(
        hazard_values[0] if hazard_values else '',
        request.args.getlist('condition') + request.args.getlist('conditions'),
    )
    valid_district_values = {
        ALL_DISTRICTS,
        UNREGISTERED_DISTRICT,
        *(f'{DISTRICT_VALUE_PREFIX}{district}' for district in get_shelter_districts()),
    }
    if selected_district not in valid_district_values:
        selected_district = ALL_DISTRICTS
    return render_template(
        'shelter_search.html',
        districts=get_shelter_districts(),
        has_unregistered_district=any(
            not isinstance(shelter, dict)
            or not isinstance(shelter.get('district'), str)
            or not shelter.get('district', '').strip()
            for shelter in shelters
        ),
        selected_district=selected_district,
        keyword=keyword,
        shelter_name=shelter_name or keyword,
        hazard_options=HAZARD_OPTIONS,
        condition_options=CONDITION_OPTIONS,
        selected_hazard=selected_hazard,
        selected_conditions=selected_conditions,
        has_districts=bool(get_shelter_districts()),
    )

# 全施設一覧ページ
@app.route('/all_shelters')
def all_shelters():
    results = [
        prepare_shelter_for_results(shelter)
        for shelter in shelters
        if isinstance(shelter, dict)
    ]
    return render_template(
        'search_results.html',
        results=results,
        is_all_shelters=True,
        selected_district=ALL_DISTRICTS,
        selected_district_label='すべての地区',
        keyword='',
        total_shelter_count=len(results),
    )


# 指示ボード：住民向けの指示を一覧で確認する
@app.route('/board')
@login_required
def board():
    resident_instructions = [i for i in instructions if i.get('target') == '住民']
    return render_template('board.html', instructions=resident_instructions)

# 検索結果ページ：templates/search_results.html を返す
@app.route('/search_results')
def search_results():
    selected_district = request.args.get('district_filter')
    if selected_district is None:
        legacy_district = request.args.get('district')
        selected_district = (
            f'{DISTRICT_VALUE_PREFIX}{legacy_district}'
            if legacy_district
            else ALL_DISTRICTS
        )
    keyword = request.args.get('keyword', '').strip()
    shelter_name = request.args.get('shelter_name', '').strip()
    hazard_values = request.args.getlist('hazard')
    if len(hazard_values) > 1:
        abort(400, description='災害種別は1つだけ指定してください。')
    selected_hazard, selected_conditions = validate_hazard_conditions(
        hazard_values[0] if hazard_values else '',
        request.args.getlist('condition') + request.args.getlist('conditions'),
    )

    valid_district_values = {
        ALL_DISTRICTS,
        UNREGISTERED_DISTRICT,
        *(f'{DISTRICT_VALUE_PREFIX}{district}' for district in get_shelter_districts()),
    }
    if selected_district not in valid_district_values:
        selected_district = ALL_DISTRICTS

    results = [
        prepare_shelter_for_results(shelter)
        for shelter in search_shelters(
            selected_district,
            keyword,
            shelter_name,
            selected_hazard,
            selected_conditions,
        )
    ]
    if selected_district == UNREGISTERED_DISTRICT:
        selected_district_label = '地区未登録'
    elif selected_district.startswith(DISTRICT_VALUE_PREFIX):
        selected_district_label = selected_district[len(DISTRICT_VALUE_PREFIX):]
    else:
        selected_district_label = 'すべての地区'

    return render_template(
        'search_results.html',
        results=results,
        is_all_shelters=False,
        selected_district=selected_district,
        selected_district_label=selected_district_label,
        keyword=keyword,
        shelter_name=shelter_name,
        selected_hazard=selected_hazard,
        selected_conditions=selected_conditions,
        hazard_options=HAZARD_OPTIONS,
        condition_options=CONDITION_OPTIONS,
        total_shelter_count=sum(isinstance(shelter, dict) for shelter in shelters),
        districts=get_shelter_districts(),
        has_unregistered_district=any(
            not isinstance(shelter, dict)
            or not isinstance(shelter.get('district'), str)
            or not shelter.get('district', '').strip()
            for shelter in shelters
        ),
    )

# JSON API：/shelters?district=地区名
@app.route('/shelters', methods=['GET'])
def get_shelters():
    district_filter = request.args.get('district_filter')
    if district_filter is None:
        district = request.args.get('district', '')
        district_filter = (
            f'{DISTRICT_VALUE_PREFIX}{district}' if district else ALL_DISTRICTS
        )
    name_values = request.args.getlist('shelter_name')
    if not name_values:
        name_values = request.args.getlist('name')
    if len(name_values) > 1:
        abort(400, description='避難所名は1つだけ指定してください。')
    hazard_values = request.args.getlist('hazard')
    if len(hazard_values) > 1:
        abort(400, description='災害種別は1つだけ指定してください。')
    hazard, conditions = validate_hazard_conditions(
        hazard_values[0] if hazard_values else '',
        request.args.getlist('condition') + request.args.getlist('conditions'),
    )
    results = search_shelters(
        district_filter,
        request.args.get('keyword', ''),
        name_values[0] if name_values else '',
        hazard,
        conditions,
    )

    if not results:
        # 見つからなければエラー JSON を返す
        return jsonify({'error': 'No shelters found'}), 404

    # 見つかったらリストを JSON で返す
    return jsonify(results)

# 気象警報・注意報API
@app.route('/api/weather_warnings')
def api_weather_warnings():
    """気象警報・注意報をJSON形式で返すAPI"""
    return jsonify(get_weather_warnings())

if __name__ == '__main__':
    app.run(debug=True, port=5000)
