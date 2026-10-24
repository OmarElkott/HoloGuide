assembly_name: Valve Cover Assembly
confidence_threshold: 0.7
camera:
  width: 512
  height: 512
  eye: [0.0, -0.35, 0.55]
  target: [0.0, 0.0, 0.02]
  fov: 55
parts:
  base:   {color: [0.55, 0.55, 0.60], shape: box,      size: [0.12, 0.12, 0.02]}
  gasket: {color: [0.10, 0.10, 0.10], shape: box,      size: [0.11, 0.11, 0.004]}
  cover:  {color: [0.15, 0.35, 0.85], shape: box,      size: [0.10, 0.10, 0.015]}
  bolts:  {color: [0.90, 0.20, 0.15], shape: cylinder, size: [0.008, 0.02]}
steps:
  - id: 1
    name: Place base
    required_parts: [base]
    instruction: Place the base plate on the fixture.
  - id: 2
    name: Insert gasket
    required_parts: [base, gasket]
    instruction: Insert the black gasket on top of the base.
  - id: 3
    name: Attach cover
    required_parts: [base, gasket, cover]
    instruction: Attach the blue cover over the gasket.
  - id: 4
    name: Add bolts
    required_parts: [base, gasket, cover, bolts]
    instruction: Add the four red bolts at the corners of the cover.