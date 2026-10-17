[English](../README.md) | [简体中文](README_ZH.md) | **日本語**

# Controller UI Haptic — Steam Frame 用スイッチ

> [!NOTE]
> SteamVR に接続した後に表示される「**Steam**」タブでは、コントローラーのハプティックフィードバックが残ります。この状況は本プラグインの制御範囲外で（ホスト PC 側の変更が必要）、修正できません。

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

1. `localStorage['sf_ui_haptic_switch']`（`'1'`/`'0'`）を書き込みます——これが唯一の真実の源で、同じページの gate が即座に反応します；
2. `__sfUiHapticSwitchSend` CDP バインディングを呼び、daemon に通知します。

daemon は `:8080` から値を読み戻し、`~/.local/state/sf-ui-haptic-switch/state.json` に永続化して、すべての `:8087` ページにプッシュします。`:8080` には決して書き戻さないため、スイッチが往復して暴走することはありません。定期ポーリングはありません：daemon は起動時、再接続時、そしてページが新しい実行コンテキストを得たときにも再同期します。

## ファイル

| パス | 役割 |
| --- | --- |
| `install.sh` / `uninstall.sh` | checkout 内で実行；unit をインストールし、リポジトリはコピーしない |
| `src/sf_ui_haptic_switch_gate.js` | 2 つのハプティックブリッジをラップ；2 つのボタン効果をゲート |
| `src/sf_ui_haptic_switch_ui.js` | スイッチを描画し、daemon に通知 |
| `src/sf_ui_haptic_switch_daemon.py` | 常駐 daemon と CDP トランスポート（Python 3、標準ライブラリのみ） |
| `src/sf_ui_haptic_switch_settings.py` | install/uninstall が使う安全な `steamvr.vrsettings` エディター |
| `installation_lib.sh` | install/uninstall 共有の定数と出力ヘルパー |
| `src/sf-ui-haptic-switch.service` | systemd daemon unit |

インストール後のレイアウト：

```text
<this checkout>/                                      ソースとスクリプト（インプレースでインストール）
/etc/systemd/system/sf-ui-haptic-switch.service       daemon unit、checkout を指す
/home/steamos/.local/state/sf-ui-haptic-switch/       永続化されたスイッチ値
~/.config/openvr/config/steamvr.vrsettings            VRWebHelper.DebuggerEnabled
```

service は checkout から `src/sf_ui_haptic_switch_daemon.py` を直接実行します。checkout を移動・削除した場合は再インストールが必要です。

`install.sh` はインプレースで実行されます：`HERE` をこの checkout に設定し、`installation_lib.sh`（`DEST="$HERE"` を設定）を source し、unit の `INSTALLATION_PATH` を checkout パスに置換して書き込みます。リポジトリのファイルはコピーしません。`uninstall.sh` は unit、状態、`VRWebHelper.DebuggerEnabled` を削除しますが、checkout は残します。

## 制限

- `:8087` は `steamvr.vrsettings` の `VRWebHelper.DebuggerEnabled = true` で有効化する必要があり（install が行います）、SteamVR の再起動が必要です。
- スイッチの値は Steam クライアントのプロファイル / 状態ファイルに保存されます。プロファイルをリセットするとスイッチは**オン**に戻ります。
- ゲートするのは 2 つのボタンホバー効果だけです。スライダーとスナップのハプティックは意図的にそのままにしています。
- ゲームや OpenXR アプリケーションが出すハプティックは完全に対象外です。

## ログと状態

```bash
journalctl -u sf-ui-haptic-switch.service -f
systemctl status sf-ui-haptic-switch.service
python3 src/sf_ui_haptic_switch_daemon.py --status
```

`--status` は永続化された値と、両エンドポイントの到達性・ページ数を表示します。daemon に `--verbose` を付けると attach/inject/push の詳細が見られます。
