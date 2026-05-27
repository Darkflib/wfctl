"""wfctl — declarative workflow controller for systemd --user units.

wfctl owns *desired state* (YAML workflow definitions). systemd owns *runtime
state* (scheduling, execution, logging, restart policy, sandboxing). This
package renders and reconciles managed ``wfctl-*.service`` / ``wfctl-*.timer``
units; it deliberately implements no scheduler, daemon, or runner of its own.
"""

__version__ = "0.1.0"
