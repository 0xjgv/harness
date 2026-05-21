//! Acceptance test runner: executes Gherkin scenarios under `tests/features/`.
//!
//! Run via `cargo harness acceptance` (or `cargo test --test acceptance`).
//! Declared in Cargo.toml with `harness = false` so cucumber owns the output.

use cucumber::{World, given, then, when};

/// Shared state for a single scenario. Mirrors behave's `context`.
#[derive(Debug, Default, World)]
struct CrateWorld {
    name: Option<&'static str>,
}

#[given("a fresh crate handle")]
fn fresh_handle(world: &mut CrateWorld) {
    world.name = None;
}

#[when("I read the crate name")]
fn read_name(world: &mut CrateWorld) {
    world.name = Some(my_project::NAME);
}

#[then("the name is not empty")]
fn name_not_empty(world: &mut CrateWorld) {
    let name = world.name.expect("crate name was never read");
    assert!(!name.is_empty(), "crate NAME is empty");
}

#[tokio::main]
async fn main() {
    CrateWorld::run("tests/features").await;
}
