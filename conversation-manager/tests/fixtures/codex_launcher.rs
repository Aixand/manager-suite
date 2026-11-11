//! Native test entry point for the Python Codex fixture on Windows.
use std::{env, fs, process::{Command, exit}};

fn main() {
    let path = env::current_exe().expect("fixture path").with_extension("fixture");
    let source = fs::read_to_string(path).expect("fixture descriptor");
    let mut lines = source.lines();
    let status = Command::new(lines.next().expect("Python path"))
        .arg("-B").arg(lines.next().expect("fixture script"))
        .args(env::args_os().skip(1)).status().expect("fixture process");
    exit(status.code().unwrap_or(1));
}
