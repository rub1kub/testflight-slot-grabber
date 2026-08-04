import AppKit
import Foundation

final class MockDelegate: NSObject, NSApplicationDelegate {
    private let appName: String
    private var window: NSWindow!
    private var statusLabel: NSTextField!
    private var actionButton: NSButton!

    init(appName: String) {
        self.appName = appName
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let frame = NSRect(x: 0, y: 0, width: 520, height: 360)
        window = NSWindow(
            contentRect: frame,
            styleMask: [.titled, .closable, .miniaturizable],
            backing: .buffered,
            defer: false
        )
        window.title = "TestFlight AX Mock"
        window.center()

        let content = NSView(frame: frame)
        window.contentView = content

        let title = NSTextField(labelWithString: appName)
        title.font = .boldSystemFont(ofSize: 28)
        title.alignment = .center
        title.frame = NSRect(x: 40, y: 255, width: 440, height: 44)
        title.setAccessibilityLabel(appName)
        content.addSubview(title)

        statusLabel = NSTextField(labelWithString: "Свободное место подтверждено")
        statusLabel.font = .systemFont(ofSize: 17)
        statusLabel.alignment = .center
        statusLabel.frame = NSRect(x: 40, y: 180, width: 440, height: 32)
        statusLabel.setAccessibilityLabel("Invitation available")
        content.addSubview(statusLabel)

        actionButton = NSButton(title: "Accept", target: self, action: #selector(acceptInvitation))
        actionButton.bezelStyle = .rounded
        actionButton.keyEquivalent = "\r"
        actionButton.identifier = NSUserInterfaceItemIdentifier("mock.accept")
        actionButton.setAccessibilityLabel("Accept")
        actionButton.frame = NSRect(x: 170, y: 90, width: 180, height: 48)
        content.addSubview(actionButton)

        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc private func acceptInvitation() {
        statusLabel.stringValue = "Invitation accepted"
        statusLabel.setAccessibilityLabel("Invitation accepted")
        actionButton.title = "Install"
        actionButton.action = #selector(installBuild)
        actionButton.identifier = NSUserInterfaceItemIdentifier("mock.install")
        actionButton.setAccessibilityLabel("Install")
    }

    @objc private func installBuild() {
        statusLabel.stringValue = "Installation completed"
        statusLabel.setAccessibilityLabel("Installation completed")
        actionButton.title = "Open"
        actionButton.action = #selector(openBuild)
        actionButton.identifier = NSUserInterfaceItemIdentifier("mock.open")
        actionButton.setAccessibilityLabel("Open")
    }

    @objc private func openBuild() {
        statusLabel.stringValue = "Mock app opened"
        statusLabel.setAccessibilityLabel("Mock app opened")
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
}

let app = NSApplication.shared
app.setActivationPolicy(.regular)
let rawArguments = Array(CommandLine.arguments.dropFirst())
let appName: String
if let index = rawArguments.firstIndex(of: "--app-name"), index + 1 < rawArguments.count {
    appName = rawArguments[index + 1]
} else {
    appName = "Telegram Messenger"
}
let delegate = MockDelegate(appName: appName)
app.delegate = delegate
app.run()
