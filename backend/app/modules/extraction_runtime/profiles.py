from __future__ import annotations

from pydantic import Field

from app.modules.extraction_runtime.contracts import (
    ExtractionCandidateKind,
    ExtractionModel,
    ExtractionOwner,
    NonEmptyString,
)


class ExtractionProfile(ExtractionModel):
    schema_version: str = "extraction_profile.v1"
    profile_ref: NonEmptyString
    owners: tuple[ExtractionOwner, ...] = Field(min_length=1)
    candidate_kinds: tuple[ExtractionCandidateKind, ...] = Field(min_length=1)
    route_key: NonEmptyString
    prompt_id: NonEmptyString
    output_schema_name: NonEmptyString
    risk_policy: NonEmptyString
    review_card_kind: NonEmptyString


class ExtractionProfileRegistry:
    def __init__(self, profiles: list[ExtractionProfile]) -> None:
        self._profiles = {profile.profile_ref: profile for profile in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("duplicate extraction profile_ref")

    def get(self, profile_ref: str) -> ExtractionProfile:
        try:
            return self._profiles[profile_ref]
        except KeyError as exc:
            raise ValueError(f"unknown extraction profile: {profile_ref}") from exc

    def resolve(self, profile_refs: list[str]) -> list[ExtractionProfile]:
        return [self.get(profile_ref) for profile_ref in profile_refs]

    def profile_refs(self) -> tuple[str, ...]:
        return tuple(self._profiles)


def default_profile_registry() -> ExtractionProfileRegistry:
    return ExtractionProfileRegistry(
        [
            ExtractionProfile(
                profile_ref="commerce_generic.v1",
                owners=("commerce_core", "business_brain", "review_only"),
                candidate_kinds=("catalog_family", "marketplace_listing"),
                route_key="structured_fast",
                prompt_id="extraction.commerce_generic",
                output_schema_name="CommerceExtractionOutput",
                risk_policy="catalog_claims_require_review",
                review_card_kind="catalog_review",
            ),
            ExtractionProfile(
                profile_ref="generic_kb.v1",
                owners=("business_brain", "review_only"),
                candidate_kinds=("kb_entry", "seller_rule"),
                route_key="structured_fast",
                prompt_id="extraction.generic_kb",
                output_schema_name="KnowledgeExtractionOutput",
                risk_policy="knowledge_claims_require_source_refs",
                review_card_kind="knowledge_review",
            ),
            ExtractionProfile(
                profile_ref="seller_voice.v1",
                owners=("business_brain", "review_only"),
                candidate_kinds=("voice_observation", "seller_rule"),
                route_key="structured_fast",
                prompt_id="extraction.seller_voice",
                output_schema_name="SellerVoiceExtractionOutput",
                risk_policy="voice_observations_are_proposed_memory",
                review_card_kind="voice_review",
            ),
            ExtractionProfile(
                profile_ref="conversation_pairs.v1",
                owners=("business_brain", "review_only"),
                candidate_kinds=("conversation_pair", "voice_observation"),
                route_key="structured_fast",
                prompt_id="extraction.conversation_pairs",
                output_schema_name="ConversationPairsExtractionOutput",
                risk_policy="pairs_are_training_memory_not_truth",
                review_card_kind="pair_review",
            ),
            ExtractionProfile(
                profile_ref="buyer_intent.v1",
                owners=("action_runtime", "review_only"),
                candidate_kinds=("buyer_intent",),
                route_key="structured_fast",
                prompt_id="extraction.buyer_intent",
                output_schema_name="BuyerIntentExtractionOutput",
                risk_policy="buyer_intent_is_signal_not_truth",
                review_card_kind="buyer_intent_review",
            ),
            ExtractionProfile(
                profile_ref="telegram_marketplace.v1",
                owners=("marketplace", "commerce_core", "review_only"),
                candidate_kinds=("marketplace_listing", "catalog_family"),
                route_key="media_rich",
                prompt_id="extraction.telegram_marketplace",
                output_schema_name="MarketplaceListingExtractionOutput",
                risk_policy="marketplace_listings_require_freshness",
                review_card_kind="marketplace_review",
            ),
        ]
    )
