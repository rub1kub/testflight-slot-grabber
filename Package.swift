// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TestFlightSlotGrabber",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "testflight-ax", targets: ["TestFlightAX"]),
        .executable(name: "testflight-ax-mock", targets: ["MockTestFlight"]),
    ],
    targets: [
        .target(
            name: "AXCore",
            linkerSettings: [
                .linkedFramework("ApplicationServices"),
                .linkedFramework("AppKit"),
            ]
        ),
        .executableTarget(name: "TestFlightAX", dependencies: ["AXCore"]),
        .executableTarget(
            name: "MockTestFlight",
            linkerSettings: [.linkedFramework("AppKit")]
        ),
    ]
)
