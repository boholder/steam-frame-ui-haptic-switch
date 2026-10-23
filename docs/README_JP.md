# Controller UI Haptic — Steam Frame 用スイッチ

[English](../README.md) | [简体中文](README_ZH.md) | **日本語**

> [!NOTE]
> 著者はすべてのコードを自身でレビュー済みです。

> [!NOTE]
> SteamVR に接続した後に表示される「**Steam**」タブと Dashboard のネイティブ frame コントロールは、既定のスイッチでは対象外です。[「任意: UI ハプティックをすべて無効化」](#任意-ui-ハプティックをすべて無効化) と [aggressive スクリプト](../scripts/aggressive-ui-haptics.sh) で、laser-mouse UI ハプティックをまとめて無効にできます。root が必要で、スライダー/スナップ/端も無効になりますが、ゲーム/OpenXR には影響しません。

Steam Frame では、コントローラーの仮想カーソルが UI ボタンに乗るたびに短いハプティック（触覚）フィードバックが発生します。ヘッドセットのメニューではこのフィードバックが常に続き、システム側にオフにする方法はありません。

このプラグインがそのスイッチを追加します。**クイックアクセスメニュー → 設定** の最下部に **Controller UI Haptic** というスイッチが表示されます。

- **オン**（デフォルト）：標準の動作——UI ボタンにホバーすると触覚が鳴ります。
- **オフ**：UI ボタンにホバーしても触覚が鳴りません——Steam クライアント UI でも SteamVR Dashboard でも同様です。

スライダーのドラッグ、スナップ、その他のフィードバックは変更しません。ボタンの enter/leave 効果だけをゲートします。

## 使い方

```bash
# Steam Frame 上で実行
git clone "https://github.com/<owner>/steam-frame-ui-haptic-switch.git"
cd steam-frame-ui-haptic-switch
sudo ./install.sh
sudo ./uninstall.sh
```

`install.sh` の実行後、Dashboard がデバッグエンドポイントを公開するように SteamVR を一度再起動してください。daemon は自動的に再試行するため、再インストールは不要です。

## 仕組み

Steam クライアント UI と SteamVR Dashboard はどちらも Chromium Embedded Framework (CEF) のページで、ローカルの DevTools エンドポイントを持っています：

| エンドポイント | 所有プロセス | ハプティックブリッジ |
| --- | --- | --- |
| `127.0.0.1:8080` | Steam クライアント（`steamwebhelper`） | `SteamClient.OpenVR.TriggerOverlayHapticEffect(effect, …)` |
| `127.0.0.1:8087` | SteamVR Dashboard（`vrwebhelper`） | `VRHTML.VROverlay.TriggerOverlayHapticEffect(handle, effect)` |

常駐 daemon（`sf_ui_haptic_switch_daemon.py`）がエンドポイントごとに 1 本のブラウザレベルの CDP 接続を維持します。`Target.setDiscoverTargets` と `Target.attachToTarget {flatten:true}` を使い、ページが現れるたびにアタッチします——ポーリングは行いません。

各ページに `sf_ui_haptic_switch_gate.js` を注入します。gate はそのページにあるハプティックブリッジをラップし、スイッチがオフの間だけ次を破棄します：

```text
1 ButtonEnter   カーソルがボタン / クリック可能な行に入る
2 ButtonLeave   カーソルがボタン / クリック可能な行から出る
```

`Snap(3)`、`Sliding(4)`、`SlidingEdge(5)` は意図的にそのままにしています。

スイッチ本体（`sf_ui_haptic_switch_ui.js`）はクイックアクセスメニューのページにのみ注入されます。切り替えると：

1. `localStorage['sf_ui_haptic_switch']`（`'true'`/`'false'`）を書き込みます——これが唯一の真実の源で、同じページの gate が即座に反応します；
2. `__sfUiHapticSwitchSend` CDP バインディングを呼び、daemon に通知します。

daemon は `:8080` から値を読み戻し、`~/.local/state/sf-ui-haptic-switch/state.json` に永続化して、すべての `:8087` ページにプッシュします。`:8080` には決して書き戻さないため、スイッチが往復して暴走することはありません。定期ポーリングはありません：daemon は起動時、再接続時、そしてページが新しい実行コンテキストを得たときにも再同期します。

## ファイル

| パス | 役割 |
| --- | --- |
| `install.sh` / `uninstall.sh` | checkout 内で実行；ユーザー unit と SteamVR リンクをインストール/削除し、リポジトリはコピーしない |
| `src/sf_ui_haptic_switch_gate.js` | 2 つのハプティックブリッジをラップ；2 つのボタン効果をゲート |
| `src/sf_ui_haptic_switch_ui.js` | スイッチを描画し、daemon に通知 |
| `src/sf_ui_haptic_switch_daemon.py` | 常駐 daemon と CDP トランスポート（Python 3、標準ライブラリのみ） |
| `src/sf_ui_haptic_switch_settings.py` | install/uninstall が使う安全な `steamvr.vrsettings` エディター |
| `installation_lib.sh` | install/uninstall 共有のパス、ユーザー/systemd ヘルパー、出力ヘルパー |
| `src/sf-ui-haptic-switch.service` | ユーザー systemd daemon unit（`steamvr.service` と連動） |

インストール後のレイアウト：

```text
<this checkout>/                                      ソースとスクリプト（インプレースでインストール）
~/.config/systemd/user/sf-ui-haptic-switch.service    ユーザー daemon unit、checkout を指す
~/.config/systemd/user/steamvr.service.wants/         有効化リンク：SteamVR と一緒に起動
~/.local/state/sf-ui-haptic-switch/                   永続化されたスイッチ値
~/.config/openvr/config/steamvr.vrsettings            VRWebHelper.DebuggerEnabled
```

service は SteamVR 自体がユーザーサービスであるため、**ユーザー** unit（`systemctl --user`）です。`steamvr.service` の後に起動し、`PartOf=steamvr.service` により SteamVR と一緒に停止・再起動します。`install.sh` は unit を書き込み、`steamvr.service.wants` シンボリックリンクを作成します。`uninstall.sh` はその両方を削除します。`sudo` で実行してもデスクトップユーザーとして動作します（`steamos` 以外の場合は `sudo SF_USER=<user> ./install.sh` で上書き）。

service は checkout から `src/sf_ui_haptic_switch_daemon.py` を直接実行します。checkout を移動・削除した場合は再インストールが必要です。

`install.sh` はインプレースで実行されます：`HERE` をこの checkout に設定し、`installation_lib.sh`（`DEST="$HERE"` を設定）を source し、unit の `INSTALLATION_PATH` を checkout パスに置換して書き込みます。リポジトリのファイルはコピーしません。`uninstall.sh` は unit、シンボリックリンク、状態、`VRWebHelper.DebuggerEnabled` を削除しますが、checkout は残します。

## 制限

- `:8087` は `steamvr.vrsettings` の `VRWebHelper.DebuggerEnabled = true` で有効化する必要があり（install が行います）、SteamVR の再起動が必要です。
- スイッチの値は Steam クライアントのプロファイル / 状態ファイルに保存されます。プロファイルをリセットするとスイッチは**オン**に戻ります。
- ゲートするのは 2 つのボタンホバー効果だけです。スライダーとスナップのハプティックは意図的にそのままにしています。
- ゲームや OpenXR アプリケーションが出すハプティックは完全に対象外です。

## 任意: UI ハプティックをすべて無効化

既定のスイッチは CEF ページの `ButtonEnter`/`ButtonLeave` だけをゲートします。Dashboard のネイティブ frame コントロールとリモート Steam タブは SteamVR のネイティブ `lasermouse` ハプティック action を使うため、まだ振動します。

`scripts/aggressive-ui-haptics.sh` は、`lasermouse.haptics` を削除したコピーを SteamOS の読み取り専用ルート上の元ファイルに bind mount して、その action を遮断します:

```bash
cd steam-frame-ui-haptic-switch
sudo ./scripts/aggressive-ui-haptics.sh apply
sudo ./scripts/aggressive-ui-haptics.sh persist   # 起動時に再作成
sudo ./scripts/aggressive-ui-haptics.sh revert    # 元に戻す
```

- **すべての** laser-mouse UI ハプティック（ホバー、スライダー、スナップ、端）を無効化。ネイティブ Dashboard コントロールとリモート Steam タブを含みます。
- ゲーム / OpenXR / Steam Input のハプティックには影響しません。
- root が必要です。`persist` は `sf-ui-haptic-binding.service` をインストールします。
- QAM スイッチではこのモードを切り替えられません。
- SteamVR 更新後は `revert` → `apply` を実行してコピーを更新してください。

## ログと状態

```bash
journalctl --user-unit sf-ui-haptic-switch.service -f
systemctl --user status sf-ui-haptic-switch.service
python3 src/sf_ui_haptic_switch_daemon.py --status
```

`--status` は永続化された値と、両エンドポイントの到達性・ページ数を表示します。daemon に `--verbose` を付けると attach/inject/push の詳細が見られます。
