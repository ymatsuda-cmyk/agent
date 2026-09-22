"""Power Automate のフロー定義とインポート用パッケージを生成する。"""

import json
import pathlib
import shutil
import zipfile

OUT = pathlib.Path("/home/claude/build/ai-agent-system/powerautomate")
OUT.mkdir(parents=True, exist_ok=True)

GROUP_ID = "e48a3383-067a-4192-8265-46871924d1f2"
REQUEST_CHANNEL = "19:-oyJ2FnxixF__MVDZ3c6dH4TWQXJDCUdqAreFbO3FuU1@thread.tacv2"
NOTIFY_CHANNEL = "19:531SffW7NFVqCkPsmNbJdagSQhKNjNNCkVoNvdPRRbc1@thread.tacv2"
DRIVE_PREFIX = "b!l98glqqOzk2L67y4G1GnjB9sMszcb2tOg5ceVAfXkHvpNNoPCisnS7V4W44zI5dX."

TEAMS_HOST = {
    "apiId": "/providers/Microsoft.PowerApps/apis/shared_teams",
    "connectionName": "shared_teams",
}
ONEDRIVE_HOST = {
    "apiId": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
    "connectionName": "shared_onedriveforbusiness",
}
AUTH = "@parameters('$authentication')"


def host(base, operation_id):
    return {**base, "operationId": operation_id}


def connection(name, connection_name, api_name):
    return {
        "connectionName": connection_name,
        "source": "Embedded",
        "id": f"/providers/Microsoft.PowerApps/apis/{name}",
        "tier": "NotSpecified",
        "apiName": api_name,
        "isProcessSimpleApiReferenceConversionAlreadyDone": False,
    }


BASE_DEFINITION = {
    "$schema": "https://schema.management.azure.com/providers/Microsoft.Logic/schemas/2016-06-01/workflowdefinition.json#",
    "contentVersion": "1.0.0.0",
    "parameters": {
        "$authentication": {"defaultValue": {}, "type": "SecureObject"},
        "$connections": {"defaultValue": {}, "type": "Object"},
    },
}


# ============================================================
# 04: reply → Teams投稿 → reply/done
# ============================================================

REPLY_CARD = """{
  "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
  "type": "AdaptiveCard",
  "version": "1.5",
  "body": [
    {
      "type": "TextBlock",
      "size": "Medium",
      "weight": "Bolder",
      "text": "Issue #@{body('JSONの解析')?['issueNumber']} @{body('JSONの解析')?['issueTitle']}",
      "wrap": true
    },
    {
      "type": "TextBlock",
      "text": "@{body('JSONの解析')?['message']}",
      "wrap": true,
      "spacing": "Medium"
    },
    {
      "type": "FactSet",
      "spacing": "Medium",
      "facts": [
        { "title": "状態", "value": "@{body('JSONの解析')?['status']}" },
        { "title": "ブランチ", "value": "@{coalesce(body('JSONの解析')?['branch'], '-')}" }
      ]
    }
  ],
  "actions": [
    {
      "type": "Action.OpenUrl",
      "title": "Issueを開く",
      "url": "@{coalesce(body('JSONの解析')?['issueUrl'], 'https://github.com')}"
    }
  ]
}"""

flow04 = {
    **BASE_DEFINITION,
    "triggers": {
        "ファイルが作成されたとき": {
            "recurrence": {"frequency": "Minute", "interval": 1},
            "metadata": {
                f"{DRIVE_PREFIX}REPLACE_WITH_REPLY_FOLDER_ID": "/work/agent/reply"
            },
            "type": "OpenApiConnection",
            "inputs": {
                "parameters": {
                    "folderId": f"{DRIVE_PREFIX}REPLACE_WITH_REPLY_FOLDER_ID",
                    "includeSubfolders": False,
                    "inferContentType": True,
                },
                "host": host(ONEDRIVE_HOST, "OnNewFileV2"),
                "authentication": AUTH,
            },
        }
    },
    "actions": {
        "ファイルコンテンツの取得": {
            "runAfter": {},
            "type": "OpenApiConnection",
            "inputs": {
                "parameters": {
                    "id": "@triggerOutputs()?['headers/x-ms-file-id']",
                    "inferContentType": True,
                },
                "host": host(ONEDRIVE_HOST, "GetFileContent"),
                "authentication": AUTH,
            },
        },
        "文字列へ変換": {
            "runAfter": {"ファイルコンテンツの取得": ["Succeeded"]},
            "type": "Compose",
            "inputs": "@base64ToString(body('ファイルコンテンツの取得')?['$content'])",
        },
        "JSONの解析": {
            "runAfter": {"文字列へ変換": ["Succeeded"]},
            "type": "ParseJson",
            "inputs": {
                "content": "@json(outputs('文字列へ変換'))",
                "schema": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string"},
                        "status": {"type": "string"},
                        "issueNumber": {"type": "integer"},
                        "issueTitle": {"type": "string"},
                        "issueUrl": {"type": "string"},
                        "branch": {"type": "string"},
                        "messageId": {"type": "string"},
                        "message": {"type": "string"},
                        "previewUrl": {"type": "string"},
                        "pullRequestUrl": {"type": "string"},
                    },
                },
            },
        },
        "返信先の判定": {
            "runAfter": {"JSONの解析": ["Succeeded"]},
            "type": "If",
            "expression": {
                "and": [
                    {
                        "not": {
                            "equals": [
                                "@coalesce(body('JSONの解析')?['messageId'], '')",
                                "",
                            ]
                        }
                    }
                ]
            },
            "actions": {
                "元の投稿へ返信": {
                    "type": "OpenApiConnection",
                    "inputs": {
                        "parameters": {
                            "poster": "Flow bot",
                            "location": "Channel",
                            "body/recipient/groupId": GROUP_ID,
                            "body/recipient/channelId": REQUEST_CHANNEL,
                            "body/messageBody": REPLY_CARD,
                            "body/replyToId": "@body('JSONの解析')?['messageId']",
                        },
                        "host": host(TEAMS_HOST, "PostCardToConversation"),
                        "authentication": AUTH,
                    },
                }
            },
            "else": {
                "actions": {
                    "通知チャネルへ投稿": {
                        "type": "OpenApiConnection",
                        "inputs": {
                            "parameters": {
                                "poster": "Flow bot",
                                "location": "Channel",
                                "body/recipient/groupId": GROUP_ID,
                                "body/recipient/channelId": NOTIFY_CHANNEL,
                                "body/messageBody": REPLY_CARD,
                            },
                            "host": host(TEAMS_HOST, "PostCardToConversation"),
                            "authentication": AUTH,
                        },
                    }
                }
            },
        },
        "処理済みへ移動": {
            "runAfter": {"返信先の判定": ["Succeeded", "Failed", "Skipped"]},
            "type": "OpenApiConnection",
            "inputs": {
                "parameters": {
                    "sourceFileId": "@triggerOutputs()?['headers/x-ms-file-id']",
                    "destinationFilePath": "/work/agent/reply/done/@{triggerOutputs()?['headers/x-ms-file-name']}",
                    "nameConflictBehavior": 1,
                },
                "host": host(ONEDRIVE_HOST, "MoveFile"),
                "authentication": AUTH,
            },
        },
    },
    "outputs": {},
}


def wrap(display_name, definition, flow_id):
    return {
        "name": flow_id,
        "id": f"/providers/Microsoft.Flow/flows/{flow_id}",
        "type": "Microsoft.Flow/flows",
        "properties": {
            "apiId": "/providers/Microsoft.PowerApps/apis/shared_logicflows",
            "displayName": display_name,
            "definition": definition,
            "connectionReferences": {
                "shared_teams": connection(
                    "shared_teams",
                    "shared-teams-33fb8072-9866-49ff-9827-eff9386aa7d6",
                    "teams",
                ),
                "shared_onedriveforbusiness": connection(
                    "shared_onedriveforbusiness",
                    "shared-onedriveforbu-20dc8025-ab3b-458e-9df4-0932c7c71290",
                    "onedriveforbusiness",
                ),
            },
            "flowFailureAlertSubscribed": False,
            "isManaged": False,
        },
    }


# ------------------------------------------------------------
# 出力
# ------------------------------------------------------------

definition_dir = OUT / "definitions"
definition_dir.mkdir(parents=True, exist_ok=True)

flow04_full = wrap(
    "04_reply→Teams通知", flow04, "a7f3c1e2-04re-4ply-9f00-agentreplyflow"
)
(definition_dir / "04_reply_to_teams.definition.json").write_text(
    json.dumps(flow04_full, ensure_ascii=False, indent=2), encoding="utf-8"
)

# インポート用パッケージ
package_root = pathlib.Path("/home/claude/build/pkg04")
if package_root.exists():
    shutil.rmtree(package_root)

FLOW_RESOURCE = "b1c2d3e4-0405-4a6b-8c9d-replyflowasset"
flow_dir = package_root / "Microsoft.Flow" / "flows" / FLOW_RESOURCE
flow_dir.mkdir(parents=True)

(flow_dir / "definition.json").write_text(
    json.dumps(flow04_full, ensure_ascii=False, indent=1), encoding="utf-8"
)
(flow_dir / "apisMap.json").write_text(
    json.dumps(
        {
            "shared_teams": "5c6c2a97-dcf2-4068-9a79-5e873ea78e36",
            "shared_onedriveforbusiness": "79671f3d-8294-43f5-9b84-31d56679f979",
        }
    ),
    encoding="utf-8",
)
(flow_dir / "connectionsMap.json").write_text(
    json.dumps(
        {
            "shared_teams": "c285df4e-9e2f-4b49-9fd4-d29281006c1b",
            "shared_onedriveforbusiness": "79b0ef1f-25ab-4627-9af7-43bf9e129fbd",
        }
    ),
    encoding="utf-8",
)
(package_root / "Microsoft.Flow" / "flows" / "manifest.json").write_text(
    json.dumps({"packageSchemaVersion": "1.0", "flowAssets": {"assetPaths": [FLOW_RESOURCE]}}),
    encoding="utf-8",
)

manifest = {
    "schema": "1.0",
    "details": {
        "displayName": "04_reply→Teams通知",
        "description": "replyフォルダのJSONをTeamsへ通知し、doneへ移動する",
        "createdTime": "2026-09-22T00:00:00.0000000Z",
        "packageTelemetryId": "0f0f0f0f-0000-4000-8000-000000000004",
        "creator": "N/A",
        "sourceEnvironment": "",
    },
    "resources": {
        FLOW_RESOURCE: {
            "type": "Microsoft.Flow/flows",
            "suggestedCreationType": "New",
            "creationType": "Existing, New, Update",
            "details": {"displayName": "04_reply→Teams通知"},
            "configurableBy": "User",
            "hierarchy": "Root",
            "dependsOn": [
                "5c6c2a97-dcf2-4068-9a79-5e873ea78e36",
                "c285df4e-9e2f-4b49-9fd4-d29281006c1b",
                "79671f3d-8294-43f5-9b84-31d56679f979",
                "79b0ef1f-25ab-4627-9af7-43bf9e129fbd",
            ],
        },
        "5c6c2a97-dcf2-4068-9a79-5e873ea78e36": {
            "id": "/providers/Microsoft.PowerApps/apis/shared_teams",
            "name": "shared_teams",
            "type": "Microsoft.PowerApps/apis",
            "suggestedCreationType": "Existing",
            "details": {"displayName": "Microsoft Teams"},
            "configurableBy": "System",
            "hierarchy": "Child",
            "dependsOn": [],
        },
        "c285df4e-9e2f-4b49-9fd4-d29281006c1b": {
            "type": "Microsoft.PowerApps/apis/connections",
            "suggestedCreationType": "Existing",
            "creationType": "Existing",
            "details": {"displayName": "Microsoft Teams connection"},
            "configurableBy": "User",
            "hierarchy": "Child",
            "dependsOn": ["5c6c2a97-dcf2-4068-9a79-5e873ea78e36"],
        },
        "79671f3d-8294-43f5-9b84-31d56679f979": {
            "id": "/providers/Microsoft.PowerApps/apis/shared_onedriveforbusiness",
            "name": "shared_onedriveforbusiness",
            "type": "Microsoft.PowerApps/apis",
            "suggestedCreationType": "Existing",
            "details": {"displayName": "OneDrive for Business"},
            "configurableBy": "System",
            "hierarchy": "Child",
            "dependsOn": [],
        },
        "79b0ef1f-25ab-4627-9af7-43bf9e129fbd": {
            "type": "Microsoft.PowerApps/apis/connections",
            "suggestedCreationType": "Existing",
            "creationType": "Existing",
            "details": {"displayName": "OneDrive for Business connection"},
            "configurableBy": "User",
            "hierarchy": "Child",
            "dependsOn": ["79671f3d-8294-43f5-9b84-31d56679f979"],
        },
    },
}

(package_root / "manifest.json").write_text(
    json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
)

zip_path = OUT / "04_reply_to_teams.zip"
with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(package_root.rglob("*")):
        if path.is_file():
            archive.write(path, path.relative_to(package_root).as_posix())

print("生成:", zip_path)
print("生成:", definition_dir / "04_reply_to_teams.definition.json")
