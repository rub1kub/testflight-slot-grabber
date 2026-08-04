import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

public struct AXCommandError: Error, CustomStringConvertible {
    public let code: Int32
    public let message: String

    public init(_ code: Int32, _ message: String) {
        self.code = code
        self.message = message
    }

    public var description: String { message }
}

public enum AXExitCode {
    public static let permissionDenied: Int32 = 10
    public static let processNotFound: Int32 = 11
    public static let elementNotFound: Int32 = 12
    public static let actionFailed: Int32 = 13
    public static let timedOut: Int32 = 14
    public static let incompatible: Int32 = 15
    public static let betaFull: Int32 = 16
    public static let unsafeTarget: Int32 = 17
    public static let screenshotFailed: Int32 = 18
}

public struct AXClientOptions {
    public let bundleID: String
    public let processName: String?
    public let allowMock: Bool
    public let expectedAppName: String
    public let maximumDepth: Int
    public let maximumNodes: Int

    public init(
        bundleID: String = AXClient.productionBundleID,
        processName: String? = nil,
        allowMock: Bool = false,
        expectedAppName: String = "Telegram Messenger",
        maximumDepth: Int = 18,
        maximumNodes: Int = 4_000
    ) {
        self.bundleID = bundleID
        self.processName = processName
        self.allowMock = allowMock
        self.expectedAppName = expectedAppName
        self.maximumDepth = maximumDepth
        self.maximumNodes = maximumNodes
    }
}

private struct ElementRecord {
    let element: AXUIElement
    let depth: Int
    let role: String
    let subrole: String
    let title: String
    let description: String
    let identifier: String
    let value: String
    let enabled: Bool

    var searchableStrings: [String] {
        [title, description, value].filter { !$0.isEmpty }
    }
}

public final class AXClient {
    public static let productionBundleID = "com.apple.TestFlight"

    private let options: AXClientOptions
    private let runningApplication: NSRunningApplication
    private let applicationElement: AXUIElement

    public static var isTrusted: Bool { AXIsProcessTrusted() }

    public static func requestPermissionPrompt() -> Bool {
        let key = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        return AXIsProcessTrustedWithOptions([key: true] as CFDictionary)
    }

    public init(options: AXClientOptions) throws {
        self.options = options
        if options.bundleID != Self.productionBundleID || options.processName != nil {
            guard options.allowMock, options.processName == "testflight-ax-mock" else {
                throw AXCommandError(
                    AXExitCode.unsafeTarget,
                    "refusing non-TestFlight process; only testflight-ax-mock is permitted with --allow-mock"
                )
            }
        }
        guard Self.isTrusted else {
            throw AXCommandError(
                AXExitCode.permissionDenied,
                "Accessibility permission is not granted to this executable or its responsible host"
            )
        }

        let application: NSRunningApplication?
        if let processName = options.processName {
            application = NSWorkspace.shared.runningApplications.first { app in
                app.localizedName == processName || app.executableURL?.lastPathComponent == processName
            }
        } else {
            application = NSRunningApplication.runningApplications(withBundleIdentifier: options.bundleID).first
        }
        guard let application else {
            throw AXCommandError(
                AXExitCode.processNotFound,
                options.processName.map { "process \($0) is not running" } ?? "TestFlight is not running"
            )
        }
        self.runningApplication = application
        self.applicationElement = AXUIElementCreateApplication(application.processIdentifier)
    }

    public var pid: pid_t { runningApplication.processIdentifier }

    private func attribute(_ element: AXUIElement, _ name: CFString) -> CFTypeRef? {
        var value: CFTypeRef?
        let error = AXUIElementCopyAttributeValue(element, name, &value)
        return error == .success ? value : nil
    }

    private func stringAttribute(_ element: AXUIElement, _ name: CFString) -> String {
        guard let raw = attribute(element, name) else { return "" }
        if let string = raw as? String { return string }
        if let number = raw as? NSNumber { return number.stringValue }
        return ""
    }

    private func boolAttribute(_ element: AXUIElement, _ name: CFString, default fallback: Bool) -> Bool {
        guard let raw = attribute(element, name), let number = raw as? NSNumber else { return fallback }
        return number.boolValue
    }

    private func children(_ element: AXUIElement) -> [AXUIElement] {
        guard let raw = attribute(element, kAXChildrenAttribute as CFString) else { return [] }
        return raw as? [AXUIElement] ?? []
    }

    private func records() throws -> [ElementRecord] {
        func makeRecord(_ element: AXUIElement, depth: Int) -> ElementRecord {
            ElementRecord(
                element: element,
                depth: depth,
                role: stringAttribute(element, kAXRoleAttribute as CFString),
                subrole: stringAttribute(element, kAXSubroleAttribute as CFString),
                title: stringAttribute(element, kAXTitleAttribute as CFString),
                description: stringAttribute(element, kAXDescriptionAttribute as CFString),
                identifier: stringAttribute(element, kAXIdentifierAttribute as CFString),
                value: stringAttribute(element, kAXValueAttribute as CFString),
                enabled: boolAttribute(element, kAXEnabledAttribute as CFString, default: true)
            )
        }

        // Action controls and the app title live in AXWindows. Traversing the
        // application's generic AXChildren also walks the macOS menu bar and
        // performs hundreds of irrelevant cross-process attribute reads before
        // a scarce Accept action. Keep the application record for diagnostics,
        // then restrict recursion to its windows. Fall back only for unusual
        // apps which do not expose AXWindows.
        let windowRoots = (attribute(applicationElement, kAXWindowsAttribute as CFString) as? [AXUIElement]) ?? []
        let roots = windowRoots.isEmpty ? children(applicationElement) : windowRoots
        var result: [ElementRecord] = [makeRecord(applicationElement, depth: 0)]
        var stack: [(AXUIElement, Int)] = roots.reversed().map { ($0, 1) }
        while let (element, depth) = stack.popLast() {
            if result.count >= options.maximumNodes { break }
            result.append(makeRecord(element, depth: depth))
            if depth < options.maximumDepth {
                for child in children(element).reversed() {
                    stack.append((child, depth + 1))
                }
            }
        }
        if result.count == 1 {
            var names: CFArray?
            let error = AXUIElementCopyAttributeNames(applicationElement, &names)
            if error == .apiDisabled || error == .notImplemented {
                throw AXCommandError(AXExitCode.permissionDenied, "Accessibility API is disabled for this process")
            }
        }
        return result
    }

    public static func normalize(_ value: String) -> String {
        value
            .folding(options: [.caseInsensitive, .diacriticInsensitive, .widthInsensitive], locale: Locale(identifier: "en_US_POSIX"))
            .split(whereSeparator: { $0.isWhitespace })
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .lowercased()
    }

    private func allText(_ records: [ElementRecord]) -> String {
        Self.normalize(records.flatMap(\.searchableStrings).joined(separator: " "))
    }

    private func expectedTitleVisible(in records: [ElementRecord]) -> Bool {
        let expected = Self.normalize(options.expectedAppName)
        return records.contains { record in
            let exactText = record.searchableStrings.contains { Self.normalize($0) == expected }
            if options.allowMock {
                return exactText
            }
            return record.identifier == "TestFlight.appDetails.title" && exactText
        }
    }

    private func findAction(
        in records: [ElementRecord],
        labels: [String],
        identifiers: [String] = []
    ) -> ElementRecord? {
        let wanted = Set(labels.map(Self.normalize))
        let wantedIdentifiers = Set(identifiers)
        let allowedRoles = Set([kAXButtonRole as String, "AXLink"])
        return records.first { record in
            record.enabled && allowedRoles.contains(record.role) && (
                wantedIdentifiers.contains(record.identifier) || record.searchableStrings.contains {
                    wanted.contains(Self.normalize($0))
                }
            )
        }
    }

    private func statusPayload(records: [ElementRecord]) -> [String: Any] {
        let text = allText(records)
        let accept = findAction(in: records, labels: Self.acceptLabels, identifiers: Self.acceptIdentifiers) != nil
        let install = findAction(in: records, labels: Self.installLabels, identifiers: Self.installIdentifiers) != nil
        let update = findAction(in: records, labels: Self.updateLabels) != nil
        let open = findAction(in: records, labels: Self.openLabels) != nil
        let full = Self.fullMarkers.contains { text.contains(Self.normalize($0)) }
        let incompatible = Self.incompatibleMarkers.contains { text.contains(Self.normalize($0)) }
        let appVisible = expectedTitleVisible(in: records)
        let state: String
        if full {
            state = "beta_full"
        } else if accept {
            state = "invitation_available"
        } else if install {
            state = "accepted_installable"
        } else if update || open {
            state = "already_joined"
        } else if appVisible && incompatible {
            state = "app_incompatible_mac"
        } else if appVisible {
            state = "app_details"
        } else {
            state = "unknown"
        }
        return [
            "ok": true,
            "state": state,
            "pid": Int(pid),
            "bundle_id": runningApplication.bundleIdentifier ?? "",
            "app_visible": appVisible,
            "expected_app_name": options.expectedAppName,
            "accept_button": accept,
            "install_button": install,
            "update_button": update,
            "open_button": open,
            "beta_full": full,
            "incompatible_mac": incompatible,
            "nodes": records.count,
        ]
    }

    public func status() throws -> [String: Any] {
        statusPayload(records: try records())
    }

    public func inspectText() throws -> String {
        let snapshot = try records()
        var lines = snapshot.enumerated().map { index, record in
            let indent = String(repeating: "  ", count: record.depth)
            let fields = [
                "role=\(record.role)",
                record.subrole.isEmpty ? nil : "subrole=\(record.subrole)",
                record.title.isEmpty ? nil : "title=\(quoted(record.title))",
                record.description.isEmpty ? nil : "description=\(quoted(record.description))",
                record.identifier.isEmpty ? nil : "identifier=\(quoted(record.identifier))",
                record.value.isEmpty ? nil : "value=\(quoted(record.value))",
                "enabled=\(record.enabled)",
            ].compactMap { $0 }.joined(separator: " ")
            return "\(indent)[\(index)] \(fields)"
        }
        lines.append("STATUS \(Self.jsonString(statusPayload(records: snapshot)))")
        return lines.joined(separator: "\n") + "\n"
    }

    private func quoted(_ value: String) -> String {
        let compact = value.replacingOccurrences(of: "\n", with: "\\n")
        return "\"\(compact.prefix(1_000))\""
    }

    public func press(kind: String, timeout: TimeInterval) throws -> [String: Any] {
        let operationStarted = Date()
        let labels: [String]
        let identifiers: [String]
        switch kind {
        case "accept":
            labels = Self.acceptLabels
            identifiers = Self.acceptIdentifiers
        case "install":
            labels = Self.installLabels + Self.updateLabels
            identifiers = Self.installIdentifiers
        case "open":
            labels = Self.openLabels
            identifiers = []
        default: throw AXCommandError(2, "unsupported action \(kind)")
        }

        let deadline = Date().addingTimeInterval(timeout)
        var lastStatus: [String: Any] = [:]
        var lookupSnapshots = 0
        while Date() < deadline {
            let snapshot = try records()
            lookupSnapshots += 1
            lastStatus = statusPayload(records: snapshot)
            let expectedAppVisible = expectedTitleVisible(in: snapshot)
            let target: ElementRecord?
            if !options.allowMock && kind == "accept" {
                // Accept is the scarce, irreversible action. The identifier below was
                // observed on a real public TestFlight invitation, so fail closed if
                // Apple changes it instead of pressing an unrelated localized button.
                target = findAction(in: snapshot, labels: [], identifiers: identifiers)
            } else {
                target = findAction(in: snapshot, labels: labels, identifiers: identifiers)
            }
            if expectedAppVisible, let target {
                let before = lastStatus
                let error = AXUIElementPerformAction(target.element, kAXPressAction as CFString)
                let pressSentAt = Date()
                guard error == .success else {
                    throw AXCommandError(AXExitCode.actionFailed, "AXPress failed with code \(error.rawValue)")
                }
                let label = target.searchableStrings.first(where: { !Self.normalize($0).isEmpty }) ?? kind
                let transitionDeadline = deadline
                var after = statusPayload(records: snapshot)
                var transitioned = false
                var stableTransitions = 0
                var transitionSnapshots = 0
                while Date() < transitionDeadline {
                    Thread.sleep(forTimeInterval: 0.10)
                    after = try statusPayload(records: records())
                    transitionSnapshots += 1
                    if kind == "accept" {
                        let acceptedStates = Set([
                            "accepted_installable",
                            "already_joined",
                            "app_incompatible_mac",
                            "app_details",
                        ])
                        let state = after["state"] as? String ?? "unknown"
                        let snapshotTransitioned =
                            (after["accept_button"] as? Bool) == false &&
                            (after["app_visible"] as? Bool) == true &&
                            acceptedStates.contains(state)
                        stableTransitions = snapshotTransitioned ? stableTransitions + 1 : 0
                        transitioned = stableTransitions >= 3
                    } else if kind == "install" {
                        transitioned = (after["open_button"] as? Bool) == true ||
                            (after["state"] as? String) == "already_joined"
                    } else {
                        transitioned = true
                    }
                    if transitioned { break }
                }
                guard transitioned else {
                    throw AXCommandError(
                        AXExitCode.actionFailed,
                        "AXPress was sent but the \(kind) control did not transition before timeout"
                    )
                }
                return [
                    "ok": true,
                    "action": kind,
                    "pressed": label,
                    "transition_verified": true,
                    "timing": [
                        "lookup_elapsed_ms": Int(pressSentAt.timeIntervalSince(operationStarted) * 1_000),
                        "transition_elapsed_ms": Int(Date().timeIntervalSince(pressSentAt) * 1_000),
                        "total_elapsed_ms": Int(Date().timeIntervalSince(operationStarted) * 1_000),
                        "lookup_snapshots": lookupSnapshots,
                        "transition_snapshots": transitionSnapshots,
                    ],
                    "pid": Int(pid),
                    "status_before": before,
                    "status_after": after,
                ]
            }
            Thread.sleep(forTimeInterval: 0.10)
        }
        if kind == "accept",
           (lastStatus["app_visible"] as? Bool) == true,
           (lastStatus["beta_full"] as? Bool) == true {
            throw AXCommandError(AXExitCode.betaFull, "\(options.expectedAppName) TestFlight beta is currently full")
        }
        if (lastStatus["incompatible_mac"] as? Bool) == true, kind == "install" {
            throw AXCommandError(AXExitCode.incompatible, "build is iOS-only and cannot be installed on this Mac")
        }
        throw AXCommandError(
            AXExitCode.elementNotFound,
            "no exact \(kind) button appeared before timeout; last state: \(lastStatus["state"] ?? "unknown")"
        )
    }

    public func screenshot(to output: URL) throws -> [String: Any] {
        guard let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] else {
            throw AXCommandError(AXExitCode.screenshotFailed, "could not enumerate on-screen windows")
        }
        let candidates: [(CGWindowID, Double)] = windows.compactMap { item in
            guard
                let ownerPID = item[kCGWindowOwnerPID as String] as? NSNumber,
                ownerPID.int32Value == pid,
                let number = item[kCGWindowNumber as String] as? NSNumber,
                let bounds = item[kCGWindowBounds as String] as? NSDictionary,
                let rect = CGRect(dictionaryRepresentation: bounds)
            else { return nil }
            return (CGWindowID(number.uint32Value), rect.width * rect.height)
        }
        guard let window = candidates.max(by: { $0.1 < $1.1 }), window.1 > 10_000 else {
            throw AXCommandError(AXExitCode.screenshotFailed, "no suitable TestFlight window found")
        }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        process.arguments = ["-x", "-l\(window.0)", output.path]
        try process.run()
        process.waitUntilExit()
        guard process.terminationStatus == 0,
              FileManager.default.fileExists(atPath: output.path),
              ((try? FileManager.default.attributesOfItem(atPath: output.path)[.size] as? NSNumber)?.intValue ?? 0) > 0
        else {
            throw AXCommandError(AXExitCode.screenshotFailed, "screencapture failed; Screen Recording permission may be missing")
        }
        return ["ok": true, "path": output.path, "window_id": Int(window.0), "pid": Int(pid)]
    }

    public static func jsonString(_ payload: [String: Any]) -> String {
        guard JSONSerialization.isValidJSONObject(payload),
              let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
              let string = String(data: data, encoding: .utf8)
        else { return "{\"ok\":false,\"message\":\"JSON encoding failed\"}" }
        return string
    }

    public static let acceptLabels = ["Accept", "Join", "Принять", "Присоединиться"]
    public static let installLabels = ["Install", "Установить"]
    public static let updateLabels = ["Update", "Обновить"]
    public static let openLabels = ["Open", "Открыть"]
    public static let acceptIdentifiers = ["TestFlight.offerButton.accept"]
    public static let installIdentifiers = ["TestFlight.offerButton.install"]
    public static let fullMarkers = [
        "This beta is full",
        "В этой программе бета-тестирования больше нет мест",
    ]
    public static let incompatibleMarkers = [
        "Incompatible with this Mac",
        "Несовместимо с этим Mac",
    ]
}
