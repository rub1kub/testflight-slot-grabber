import AXCore
import Darwin
import Foundation

struct Arguments {
    let command: String
    let bundleID: String
    let processName: String?
    let allowMock: Bool
    let appName: String
    let json: Bool
    let timeout: TimeInterval
    let output: String?

    init() throws {
        let raw = Array(CommandLine.arguments.dropFirst())
        guard let first = raw.first else { throw AXCommandError(2, "missing command") }
        command = first
        var bundle = AXClient.productionBundleID
        var process: String?
        var mock = false
        var expectedAppName = "Telegram Messenger"
        var wantsJSON = false
        var timeoutValue: TimeInterval = 10
        var outputValue: String?
        var index = 1
        while index < raw.count {
            switch raw[index] {
            case "--bundle-id":
                index += 1
                guard index < raw.count else { throw AXCommandError(2, "--bundle-id needs a value") }
                bundle = raw[index]
            case "--process-name":
                index += 1
                guard index < raw.count else { throw AXCommandError(2, "--process-name needs a value") }
                process = raw[index]
            case "--app-name":
                index += 1
                guard index < raw.count, !raw[index].trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
                    throw AXCommandError(2, "--app-name needs a non-empty value")
                }
                expectedAppName = raw[index]
            case "--allow-mock": mock = true
            case "--json": wantsJSON = true
            case "--timeout":
                index += 1
                guard index < raw.count, let parsed = TimeInterval(raw[index]), parsed > 0, parsed <= 120 else {
                    throw AXCommandError(2, "--timeout must be in 0..120")
                }
                timeoutValue = parsed
            case "--output":
                index += 1
                guard index < raw.count else { throw AXCommandError(2, "--output needs a path") }
                outputValue = raw[index]
            default: throw AXCommandError(2, "unknown argument \(raw[index])")
            }
            index += 1
        }
        bundleID = bundle
        processName = process
        allowMock = mock
        appName = expectedAppName
        json = wantsJSON
        timeout = timeoutValue
        output = outputValue
    }
}

func emit(_ payload: [String: Any]) {
    print(AXClient.jsonString(payload))
}

do {
    let arguments = try Arguments()
    if arguments.command == "permission" {
        emit(["ok": AXClient.isTrusted, "trusted": AXClient.isTrusted, "message": AXClient.isTrusted ? "Accessibility granted" : "Accessibility not granted"])
        exit(AXClient.isTrusted ? 0 : AXExitCode.permissionDenied)
    }
    if arguments.command == "prompt-permission" {
        let trusted = AXClient.requestPermissionPrompt()
        emit(["ok": trusted, "trusted": trusted, "message": trusted ? "Accessibility granted" : "Permission prompt opened"])
        exit(trusted ? 0 : AXExitCode.permissionDenied)
    }
    if arguments.command == "self-test" {
        let normalized = AXClient.normalize("  ПРИНЯТЬ\n")
        let ok = normalized == "принять" && AXClient.acceptLabels.contains("Join") && AXClient.installLabels.contains("Установить")
        emit(["ok": ok, "normalized": normalized, "production_bundle": AXClient.productionBundleID])
        exit(ok ? 0 : 1)
    }

    let client = try AXClient(
        options: AXClientOptions(
            bundleID: arguments.bundleID,
            processName: arguments.processName,
            allowMock: arguments.allowMock,
            expectedAppName: arguments.appName
        )
    )
    switch arguments.command {
    case "inspect":
        let tree = try client.inspectText()
        if let output = arguments.output {
            try tree.write(toFile: output, atomically: true, encoding: .utf8)
            emit(["ok": true, "path": output, "pid": Int(client.pid)])
        } else {
            print(tree, terminator: "")
            emit(["ok": true, "pid": Int(client.pid)])
        }
    case "status": emit(try client.status())
    case "accept": emit(try client.press(kind: "accept", timeout: arguments.timeout))
    case "install": emit(try client.press(kind: "install", timeout: arguments.timeout))
    case "open": emit(try client.press(kind: "open", timeout: arguments.timeout))
    case "screenshot":
        guard let output = arguments.output else { throw AXCommandError(2, "screenshot requires --output") }
        emit(try client.screenshot(to: URL(fileURLWithPath: output)))
    default: throw AXCommandError(2, "unknown command \(arguments.command)")
    }
} catch let error as AXCommandError {
    emit(["ok": false, "message": error.message, "exit_code": Int(error.code)])
    fputs("testflight-ax: \(error.message)\n", stderr)
    exit(error.code)
} catch {
    emit(["ok": false, "message": String(describing: error), "exit_code": 1])
    fputs("testflight-ax: \(error)\n", stderr)
    exit(1)
}
