# LAS点群処理ツール

CHCNav RS10 / Scaniverse のLASデータから  
**展開図・平面図をDXFで出力する**Pythonツールです。

---

## 対応機器

| 機器 | 展開図 | 平面図 | 写真テクスチャ |
|------|-----------|--------|--------------|
| CHCNav RS10 | ✅ | ✅ | ✅（フェーズ2） |
| Scaniverse | ✅ | ✅ | ❌ |

---

## 動作環境

- Windows 11
- Python 3.10 以上

---

## インストール

```bash
pip install laspy numpy scipy ezdxf Pillow matplotlib
```

> `matplotlib` は縦断図・横断図（線画DXF）のプレビュー画像生成に使います。

---

## フォルダ構成

```
las_tool/
├── las_processor.py   # 点群処理コア
├── config.py          # 設定モジュール（カスタマイズ用）
├── config.json        # 設定ファイル（編集するとここが反映される）
├── gui_app.py         # GUIアプリ（tkinter）
├── docs/
│   ├── 要件定義書.md
│   └── README.md（本ファイル）
└── output/            # DXF・PNG出力先
```

---

## 使い方（GUI）

いちばん簡単なのはGUIです。

```bash
python gui_app.py
```

基本の流れは **設定 → 実行（作成・確認） → 保存（外部出力）** です。

1. 「LAS選択…」でファイルを指定（無ければ「ダミー生成」でテストデータ作成）
2. **現場名 / 工事名**を入力（現場ごとに作成図面を記憶します。過去の現場はプルダウンから選択可）
3. 対象種別（擁壁／トンネル／水路（両壁））を選ぶ
4. 作成したい図面（展開図／平面図／縦断図／横断図）にチェック・パラメータ調整
5. **「実行（作成・確認）」**を押す → 図面を**現場別の作業フォルダ**に作成し、プレビュー表示（まだ外部出力しない）
6. 内容を確認し、問題なければ **「保存…」** を押す → ダイアログで
   - **保存先フォルダ**を選択
   - 出力する図面を最終チェック（作成済みの中から外せる）
   - 出力方法：**個別ファイル**／**1枚のDXFにまとめる**（一覧シート）／**ZIPに各図面を格納**
   - 「保存」で外部出力

### 現場メモリ
- 「実行」すると、現場名ごとのフォルダ（`works/<現場名>/`）に作成図面が保存・記憶されます
- 現場名プルダウンで過去の現場を選び、**「履歴」**ボタンでその現場の作成図面フォルダを開いて確認できます
- 「作業フォルダを開く」で直近の作業フォルダを開けます

> 出力方法：**個別＝図面ごとにDXF＋PNG**／**1枚にまとめる＝全図面を画像として縦に並べた1つのDXF（一覧用）**／**ZIP＝各図面のDXF＋PNGをまとめた `…_図面一式.zip`**。
> 個別・ZIPは各図面の完全なDXF（線画＋実寸埋め込み画像）。1枚まとめは画像ベースの一覧シートです。

プレビューは **マウスホイールで拡大縮小／ドラッグで移動**、ボタンで「＋／－／原寸／幅／高さ／全体」、さらに **回転（⟲⟳90°・角度直接入力→適用）** ができます。
既定は **原寸（あるがまま）表示**で、縦・横とも固定せず、画面外にはみ出した分は**縦・横スクロールバー**（横は Shift+ホイールでも）で送れます。「幅」「高さ」「全体」でウィンドウに合わせられます。
展開図・平面図はスキャン画像、**縦断図・横断図は線画DXFを画像化**してプレビュー表示します（複数図面を出力した場合は最初の1枚を表示）。
「工事名（表題欄）」に入力した内容は擁壁展開図の表題欄に反映されます。
「設定保存／読込」で現在のパラメータを config.json に保存・復元できます。

> 線画プレビューの日本語タイトルは環境により文字化けすることがありますが、DXF本体やCAD表示には影響しません（線画は正しく描画されます）。
> 点群が疎で画像に砂状の隙間が出る場合は `fill_gaps`（隙間補間）で埋まります。実スキャンは点が密なので通常は不要です。

> **DXFへの図面化（スキャン画像の埋め込み）**：展開図・平面図のDXFには、スキャン画像が実寸（メートル単位）で貼り付けられます（`embed_raster`）。AutoCADはこの画像を**相対パスで参照**するため、**DXFと同名の `_プレビュー.png` を同じフォルダに置いたまま**にしてください（移動するときは両方一緒に）。クラック等はこの画像の上からなぞれます。

---

## 使い方（スクリプト／フェーズ1）

### ダミーデータで動作確認

```python
from las_processor import (
    generate_dummy_wall_las,
    generate_dummy_tunnel_las,
    load_las,
    generate_elevation_wall,
    generate_elevation_tunnel,
    generate_plan_view,
    export_elevation_dxf,
    export_plan_dxf,
)

# 1. ダミーLAS生成
generate_dummy_wall_las("test_wall.las")

# 2. 読み込み
pts = load_las("test_wall.las")

# 3. 展開図生成
img, meta = generate_elevation_wall(pts, axis="y")

# 4. DXF出力
export_elevation_dxf(img, meta, "output/elevation.dxf", title="擁壁展開図")

# 5. 平面図DXF出力
plan_img, plan_meta = generate_plan_view(pts)
export_plan_dxf(pts, plan_meta, "output/plan.dxf", title="平面図")
```

### 実データ（RS10）で使う場合

```python
# CoPre処理済みLASを指定するだけ
pts = load_las("C:/現場データ/project01/output.las")
```

---

## カスタマイズ（config.json）

解像度・グリッドピッチ・色・縦スケールなどは、コードを触らず **`config.json`** を編集するだけで変更できます。

### 設定ファイルの作り方

```bash
# config.json のテンプレートを生成
python config.py
```

生成された `config.json` をテキストエディタで開いて値を書き換えるだけです。

### 主な設定項目

| キー | 内容 | 既定値 |
|------|------|--------|
| `elevation_resolution` | 展開図の解像度(m/px)。小さいほど高精細・重い | 0.01 |
| `tunnel_resolution` | トンネル展開の解像度(m/px) | 0.01 |
| `tunnel_seam` | トンネル展開の切り口 `crown`（天井で切る/インバート中央＝魚の開き）/`invert`（底/天端中央） | crown |
| `plan_resolution` | 平面図の解像度(m/px) | 0.05 |
| `elevation_max_pix` / `plan_max_pix` | 画像の最大ピクセル（メモリ保護） | 8000 / 6000 |
| `wall_thickness` | 壁面切り出し厚み(m) | 0.3 |
| `background_color` | 点群なし部分の背景色（RGB各0〜1） | [0.05, 0.08, 0.15] |
| `fill_gaps` / `fill_radius_px` | 点が疎な箇所の画素の穴を近傍色で補間（砂嵐対策）/ 補間距離(px) | true / 4 |
| `elevation_grid` / `plan_grid` | 展開図・平面図のグリッドピッチ(m) | 1.0 / 5.0 |
| `profile_grid_h_short` / `_long` / `_threshold` | 縦断図 横グリッド（短／長／切替延長m） | 5.0 / 10.0 / 100.0 |
| `profile_grid_v` | 縦断図 縦グリッドピッチ(m) | 1.0 |
| `profile_n_slices` | 縦断図の延長分割数（多いほど滑らか） | 100 |
| `profile_slice_width` | XY追従モードのスライス幅(m) | 0.5 |
| `profile_v_scale` | 縦断図の縦スケール倍率（1=実寸、5で5倍強調） | 1.0 |
| `profile_tunnel_axis` | 延長方向 `"z"`（標準）/ `"xy"`（センター追従） | "z" |
| `cross_section_thickness` | 横断図スライス厚み(m) | 0.2 |
| `cross_section_axis` | 横断図の延長軸（トンネル`"z"`／擁壁`"x"`等） | "z" |
| `cross_section_n_angles` | トンネル断面の角度分割数（リング解像度） | 360 |
| `cross_section_n_vbins` | 擁壁断面の高さ方向ビン数 | 200 |
| `cross_section_pitch` | 複数断面の測点ピッチ(m) | 10.0 |
| `cross_section_grid` | 横断図のグリッドピッチ(m) | 1.0 |
| `cross_section_color` | 断面輪郭線の色 | 1 |
| `plan_max_plot_points` | 平面図に描く点群の最大数 | 50000 |
| `dxf_version` / `dim_style` | DXFバージョン・寸法スタイル名 | R2010 / EZ_M_100_H25_CM |
| `embed_raster` | 展開図/平面図のスキャン画像をDXFに実寸で埋め込む | true |
| `station_pitch` | 擁壁展開図の測点ピッチ(m)（横軸 No.目盛） | 5.0 |
| `station_zero_no` | 起点の測点番号（No.） | 0 |
| `elevation_axis_pitch` | 展開図の標高軸（縦）目盛ピッチ(m) | 1.0 |
| `extract_outline` | 天端線・地盤線を画像から抽出して描く | true |
| `draw_title_block` | 表題欄を描く | true |
| `drawing_scale` / `project_name` / `author` | 縮尺表記 / 工事名 / 作成者（表題欄） | 1:100 / "" / 岳_デザイン |
| `crown_line_color` / `ground_line_color` | 天端線 / 地盤線の色 | 1 / 2 |
| `frame_color` / `grid_color` / `title_color` / `point_color` | 外枠・グリッド・タイトル・点群の色 | 7 / 8 / 3 / 4 |
| `crown_color` / `left_color` / `right_color` / `floor_color` / `center_color` | 縦断図 天端／左壁／右壁／地面／センターの色 | 1 / 5 / 3 / 2 / 7 |

> 色はAutoCADカラーインデックス（1=赤 2=黄 3=緑 4=水 5=青 6=紫 7=白 8=灰）。

### コードから設定を使う

```python
from config import Config
from las_processor import generate_plan_view

# config.json を読み込む（無ければ既定値）
cfg = Config.load("config.json")

# 一部だけその場で上書きすることも可能
cfg = Config(plan_grid=10.0, profile_v_scale=5.0)

plan_img, plan_meta = generate_plan_view(pts, cfg=cfg)
```

`cfg=` を渡さない場合は `config.json`（無ければ組み込み既定値）が自動で使われます。

---

## 出力DXFの内容

### 擁壁展開図（測量様式）
- 横軸：**測点(No.)＋追加距離**（展開長）
- 縦軸：**実標高(m)**
- **天端線・地盤線**を点群画像から自動抽出
- スキャン画像を実寸で埋め込み（クラック等をなぞれる）
- **表題欄**（工事名・図面名・縮尺・作成者・作成日）・縮尺表記

### 展開図（トンネル）＝魚の開き（アライメント追従）
- トンネル/暗渠の内面を**アライメント（中心線）に沿って円周展開**（魚の開き）
- **延長方向はXY平面の主成分で自動判定**（斜め・任意方向の通路に対応）
- **縦断勾配・曲がりは測点ごとの断面中心で吸収**（実データ対応）
- **横軸＝展開延長**、**縦軸＝円周の展開（実寸・全表示）**。各点を実半径位置で展開するので、断面サイズ（壁高）の違いが縦方向にそのまま表れ、坑口など大断面も切り捨てずすべて表示する
- **展開基準を選択可（GUIの「展開基準」）**：
  - **天井基準**（`tunnel_seam=crown`）：天端で切ってインバート（底）を中央に展開
  - **地面基準**（`tunnel_seam=invert`）：底で切って天端を中央に展開
- 関数：`generate_tunnel_development`（軸並行版 `generate_elevation_tunnel` も残置）

### 平面図
- 5mピッチグリッド線
- 座標値ラベル
- 点群分布（間引きプロット）

### 縦断図
- 天端／左壁／右壁／地面／センターをレイヤー別に描画
- グリッド・凡例・縦スケール注記

### 横断図
- トンネル：閉じたリング断面／擁壁：壁面プロファイル
- 外枠・グリッド・測点（STA）ラベル
- 単一断面、または複数断面を並べて出力

### アライメント自動整列
- 全図面（展開図・平面図・縦断図・横断図）を**中心線（XY主成分）に沿わせて**生成
- 斜め・任意方向に延びる構造物でも、延長方向を横軸にした正しい図面になる
- GUIの「アライメント自動整列」チェック（既定ON）。OFF時は「壁面の水平軸」で軸指定
- 壁高が異なる箇所は、展開図（実標高）と縦断図（天端・左壁・右壁・インバートのライン）で高さ差として表れる

---

## 開発ロードマップ

- [x] フェーズ1：コア処理モジュール（展開図・平面図・縦断図・横断図）
- [x] フェーズ1：設定カスタマイズ（config.json）
- [x] フェーズ1：GUIアプリ（tkinter）
- [ ] フェーズ2：RS10写真テクスチャ投影
- [ ] フェーズ3：クラック自動検出

---

## 注意事項

- 出力DXFはAutoCAD / Bluetrendでの仕上げ確認が必要です
- 成果図面としての提出前に必ず人的チェックを行ってください
- Scaniverseデータは写真テクスチャ非対応です（点群カラーのみ）

---

## 問い合わせ

西田岳司（岳_デザイン）  
CrowdWorks / Lancers
