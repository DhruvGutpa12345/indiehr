"""Keycloak identity for IndieHR hospitals (P1-T3, TENANCY_DESIGN.md §4, ADR-008).

Standard library only, with no Django imports: the same code runs in the backend container
(management commands), on the host (scripts/keycloak-test.sh) and on a bare CI runner.
"""
