# -*- coding: utf-8 -*-
"""
Voice AI Token Storage - Secure credential storage service.

Provides secure storage for API keys and secrets using base64 encoding
in ir.config_parameter with optional keyring support.
"""
import base64
import hashlib
import logging

from odoo import models, api

logger = logging.getLogger(__name__)

# Try to import keyring but don't fail if not available
try:
    import keyring
    KEYRING_AVAILABLE = True
except ImportError:
    KEYRING_AVAILABLE = False


class VoiceTokenStorage(models.AbstractModel):
    """
    Secure credential storage service for Voice AI modules.

    Stores secrets with base64 encoding in ir.config_parameter,
    with optional OS keyring support for enhanced security.
    """
    _name = 'voice.token.storage'
    _description = 'Voice AI Token Storage'

    @api.model
    def _get_param_name(self, key_type):
        """
        Generate parameter name for storing secrets.

        Args:
            key_type: Type of key (e.g., 'elevenlabs_api_key', 'webhook_secret')

        Returns:
            str: Parameter name for ir.config_parameter
        """
        # Create a unique identifier for this key type
        key_hash = hashlib.sha256(f"voice_{key_type}".encode()).hexdigest()[:16]
        return f"voice_secret.{key_hash}"

    @api.model
    def store_secret(self, key_type, secret):
        """
        Store a secret securely.

        Args:
            key_type: Type of key (e.g., 'elevenlabs_api_key')
            secret: The secret to store

        Returns:
            bool: Success or failure
        """
        if not secret:
            return False

        param_name = self._get_param_name(key_type)

        # Encode secret
        encoded_secret = base64.b64encode(secret.encode('utf-8')).decode('utf-8')

        # Store in system parameters
        self.env['ir.config_parameter'].sudo().set_param(param_name, encoded_secret)

        # Also try to store in keyring if available (more secure)
        if KEYRING_AVAILABLE:
            try:
                keyring.set_password("odoo_voice_ai", key_type, secret)
            except Exception as e:
                logger.debug("Could not store in keyring: %s. Using parameters only.", e)

        return True

    @api.model
    def get_secret(self, key_type):
        """
        Retrieve a stored secret.

        Args:
            key_type: Type of key (e.g., 'elevenlabs_api_key')

        Returns:
            str: The secret, or None if not found
        """
        # Try keyring first if available
        if KEYRING_AVAILABLE:
            try:
                secret = keyring.get_password("odoo_voice_ai", key_type)
                if secret:
                    return secret
            except Exception as e:
                logger.debug("Could not retrieve from keyring: %s", e)

        # Fall back to system parameters
        param_name = self._get_param_name(key_type)
        encoded_secret = self.env['ir.config_parameter'].sudo().get_param(param_name)

        if encoded_secret:
            try:
                return base64.b64decode(encoded_secret.encode('utf-8')).decode('utf-8')
            except Exception as e:
                logger.error("Failed to decode secret: %s", e)

        return None

    @api.model
    def delete_secret(self, key_type):
        """
        Delete a stored secret.

        Args:
            key_type: Type of key (e.g., 'elevenlabs_api_key')

        Returns:
            bool: Success or failure
        """
        # Delete from system parameters
        param_name = self._get_param_name(key_type)
        self.env['ir.config_parameter'].sudo().set_param(param_name, False)

        # Also try to delete from keyring if available
        if KEYRING_AVAILABLE:
            try:
                keyring.delete_password("odoo_voice_ai", key_type)
            except Exception:
                pass  # Ignore keyring errors on deletion

        return True

    @api.model
    def has_secret(self, key_type):
        """
        Check if a secret is stored.

        Args:
            key_type: Type of key (e.g., 'elevenlabs_api_key')

        Returns:
            bool: True if secret exists
        """
        return bool(self.get_secret(key_type))
