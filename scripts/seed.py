from app.db import SessionLocal
from app.embeddings import embedding_client
from app.models import (
    Booking,
    Club,
    ConversationMessage,
    ConversationSession,
    KnowledgeChunk,
    Member,
    MemberAttribute,
    PaymentAttempt,
)
from app.services.matching_service import build_member_profile_text
from scripts.init_db import init_db


def make_member(db, club_id, n, role="member"):
    m = Member(
        club_id=club_id,
        name=f"{club_id.title()} Member {n}",
        email=f"{club_id}.member{n}@example.com",
        token=f"{club_id}-member-{n}",
        role=role,
    )
    db.add(m)
    db.flush()
    return m


def add_message_and_attr(db, member, body, kind, text, confidence, restricted=False):
    msg = ConversationMessage(member_id=member.id, club_id=member.club_id, body=body)
    db.add(msg)
    db.flush()
    attr = MemberAttribute(
        member_id=member.id,
        club_id=member.club_id,
        kind=kind,
        text=text,
        confidence=confidence,
        restricted=restricted,
        source_message_id=msg.id,
    )
    db.add(attr)


def add_chunk(db, club_id, title, body):
    db.add(
        KnowledgeChunk(
            club_id=club_id,
            title=title,
            body=body,
            embedding=embedding_client.embed(body),
        )
    )


def seed() -> None:
    init_db()
    db = SessionLocal()

    for model in [ConversationSession, PaymentAttempt, Booking, MemberAttribute, ConversationMessage, KnowledgeChunk, Member, Club]:
        db.query(model).delete()
    db.commit()

    db.add_all([Club(id="riverside", name="Riverside Club"), Club(id="oakhurst", name="Oakhurst Club")])
    db.commit()

    db.add_all(
        [
            Member(
                club_id="riverside",
                name="Riverside Admin",
                email="riverside.admin@example.com",
                token="riverside-admin",
                role="admin",
            ),
            Member(
                club_id="oakhurst",
                name="Oakhurst Admin",
                email="oakhurst.admin@example.com",
                token="oakhurst-admin",
                role="admin",
            ),
        ]
    )
    db.commit()

    riverside_members = [make_member(db, "riverside", i) for i in range(1, 7)]
    oakhurst_members = [make_member(db, "oakhurst", i) for i in range(1, 7)]
    db.commit()

    add_message_and_attr(
        db,
        riverside_members[0],
        "I'm raising a seed round for my startup, could use intros to angels.",
        "need",
        "raising a seed round, looking for angel investors",
        0.9,
    )
    add_message_and_attr(
        db,
        riverside_members[0],
        "I might raise again next year too, but honestly not sure yet.",
        "need",
        "might be raising again next year",
        0.3,
    )
    add_message_and_attr(
        db,
        riverside_members[1],
        "I've angel invested in a dozen seed-stage startups over the years.",
        "offer",
        "angel investor, has backed a dozen seed-stage startups",
        0.95,
    )
    add_message_and_attr(
        db,
        riverside_members[2],
        "Between us, I've been managing anxiety and I prefer quieter events.",
        "context",
        "manages anxiety, prefers quiet low-key venues",
        0.9,
        restricted=True,
    )
    add_message_and_attr(
        db,
        riverside_members[3],
        "Looking for a regular padel partner, I play most Saturday mornings.",
        "interest",
        "looking for a padel partner, plays Saturday mornings",
        0.85,
    )

    add_message_and_attr(
        db,
        oakhurst_members[0],
        "I used to run a hospitality business before switching to consulting.",
        "context",
        "former hospitality business owner, now a consultant",
        0.85,
    )
    add_message_and_attr(
        db,
        oakhurst_members[1],
        "I need a fractional CFO for about ten hours a month.",
        "need",
        "needs a fractional CFO, roughly ten hours a month",
        0.9,
    )
    add_message_and_attr(
        db,
        oakhurst_members[2],
        "I've worked as a fractional CFO for three portfolio companies.",
        "offer",
        "fractional CFO experience across three portfolio companies",
        0.95,
    )
    add_message_and_attr(
        db,
        oakhurst_members[3],
        "I was recently diagnosed with a chronic condition, still adjusting my routine.",
        "context",
        "recently diagnosed with a chronic condition",
        0.9,
        restricted=True,
    )

    db.commit()

    add_chunk(db, "riverside", "Guest fees", "Riverside guest fees are $50 per visit, waived for members' immediate family.")
    add_chunk(db, "riverside", "Dress code", "Riverside's dress code is smart casual after 6pm, resort wear during the day.")
    add_chunk(db, "riverside", "Opening hours", "Riverside is open 7am to 11pm daily, kitchen closes at 10pm.")
    add_chunk(db, "riverside", "Cancellation policy", "Riverside bookings can be cancelled up to 24 hours ahead for a full refund.")

    add_chunk(db, "oakhurst", "Guest fees", "Oakhurst guest fees are $75 per visit, capped at two guests per member per month.")
    add_chunk(db, "oakhurst", "Dress code", "Oakhurst requires collared shirts in all dining areas, no exceptions.")
    add_chunk(db, "oakhurst", "Opening hours", "Oakhurst is open 6am to midnight, the pool closes at 9pm.")
    add_chunk(db, "oakhurst", "Cancellation policy", "Oakhurst bookings are non-refundable within 48 hours of the reservation.")

    db.commit()

    db.add(
        Booking(
            member_id=riverside_members[0].id,
            club_id="riverside",
            description="Saturday dinner for 2",
            amount_cents=8000,
            status="pending",
        )
    )
    db.add(
        Booking(
            member_id=oakhurst_members[0].id,
            club_id="oakhurst",
            description="Sunday brunch for 4",
            amount_cents=12000,
            status="pending",
        )
    )
    db.commit()

    for m in riverside_members + oakhurst_members:
        m.profile_embedding = embedding_client.embed(build_member_profile_text(m, db))
    db.commit()

    print("Seed complete.\n")
    print("Tokens (use as the X-Member-Token header):")
    print("  riverside admin: riverside-admin")
    print("  oakhurst admin:  oakhurst-admin")
    for m in riverside_members:
        print(f"  {m.name}: {m.token}")
    for m in oakhurst_members:
        print(f"  {m.name}: {m.token}")

    db.close()


if __name__ == "__main__":
    seed()
