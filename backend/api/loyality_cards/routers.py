from fastapi import APIRouter, Depends, HTTPException
from database.db import database, loyality_cards, contragents, organizations, loyality_settings, users, users_cboxes_relation
import api.loyality_cards.schemas as schemas
from sqlalchemy import desc, or_

from functions.helpers import datetime_to_timestamp, get_entity_by_id, add_status, get_entity_by_id_cashbox, contr_org_ids_to_name, get_entity_by_id_and_created_by, get_filters_cards

from ws_manager import manager
from functions.helpers import get_user_by_token
from datetime import datetime

from random import randint
from sqlalchemy import func, select
from fuzzywuzzy import fuzz

router = APIRouter(tags=["loyality_cards"])


@router.get("/loyality_cards/{idx}", response_model=schemas.LoyalityCardGet)
async def get_loyality_card_by_id(token: str, idx: int):
    """Получение карты по ID"""
    user = await get_user_by_token(token)
    loyality_cards_db_q = loyality_cards.select().where(loyality_cards.c.is_deleted == False, loyality_cards.c.cashbox_id == user.cashbox_id, loyality_cards.c.id == idx)
    loyality_cards_db = await database.fetch_one(loyality_cards_db_q)
    loyality_cards_db = datetime_to_timestamp(loyality_cards_db)
    loyality_cards_db = await contr_org_ids_to_name(loyality_cards_db)

    return loyality_cards_db


@router.get("/loyality_cards/", response_model=schemas.CountRes)
async def get_cards(token: str, limit: int = 100, offset: int = 0, filters_q: schemas.LoyalityCardFilters = Depends()):
    """Получение списка карт"""
    user = await get_user_by_token(token)

    filters = get_filters_cards(loyality_cards, filters_q)
    filters_dict = filters_q.dict(exclude_none=True)

    if filters_dict.get("contragent_name"):
        q = contragents.select().where(contragents.c.name.ilike(f'%{filters_dict.get("contragent_name")}%'), contragents.c.cashbox == user.cashbox_id)
        finded_contrs = await database.fetch_all(q)
        
        filters.append(
            loyality_cards.c.contragent_id.in_([contr.id for contr in finded_contrs])
        )

    if filters_dict.get("phone_number"):
        q = contragents.select().where(contragents.c.phone.ilike(f'%{filters_dict.get("phone_number")}%'), contragents.c.cashbox == user.cashbox_id)
        finded_contrs = await database.fetch_all(q)
        
        filters.append(
            loyality_cards.c.contragent_id.in_([contr.id for contr in finded_contrs])
        )

    if filters_dict.get("organization_name"):
        q = organizations.select().where(organizations.c.short_name.ilike(f'%{filters_dict.get("organization_name")}%'), organizations.c.owner == user.id)
        finded_orgs = await database.fetch_all(q)
        
        filters.append(
            loyality_cards.c.organization_id.in_([org.id for org in finded_orgs])
        )

    if filters_dict.get("created_by_id"):
        q = users.select().where(users.c.id == int(filters_dict.get("created_by_id")))
        user_by_filter = await database.fetch_one(q)

        q = users_cboxes_relation.select().where(users_cboxes_relation.c.cashbox_id == user.cashbox_id, users_cboxes_relation.c.user == user_by_filter.id, users_cboxes_relation.c.status == True)
        acc_by_phone = await database.fetch_one(q)

        if acc_by_phone:
            filters.append(
                loyality_cards.c.created_by_id == acc_by_phone.id
            )
        else:
            raise HTTPException(404, "Такого пользователя не существует")

    if filters:
        query = (
            loyality_cards.select()
            .where(
                loyality_cards.c.cashbox_id == user.cashbox_id,
                loyality_cards.c.is_deleted.is_not(True)
            )
            .order_by(desc(loyality_cards.c.id))
            .filter(*filters)
            .limit(limit)
            .offset(offset)
        )

    else:
        query = (
            loyality_cards.select()
            .where(
                loyality_cards.c.cashbox_id == user.cashbox_id,
                loyality_cards.c.is_deleted.is_not(True)
            )
            .order_by(desc(loyality_cards.c.id))
            .limit(limit)
            .offset(offset)
        )


    loyality_cards_db = await database.fetch_all(query)
    loyality_cards_db = [*map(datetime_to_timestamp, loyality_cards_db)]
    loyality_cards_db = [*map(contr_org_ids_to_name, loyality_cards_db)]

    loyality_cards_db = [await instance for instance in loyality_cards_db]

    c = select(func.count(loyality_cards.c.id)).where(loyality_cards.c.cashbox_id == user.cashbox_id, loyality_cards.c.is_deleted.is_not(True)).filter(*filters)
    count = await database.fetch_one(c)

    return {"result": loyality_cards_db, "count": count.count_1} # LOCAL




@router.post("/loyality_cards/", response_model=schemas.LoyalityCardsList)
async def new_loyality_card(token: str, loyality_card_data: schemas.LoyalityCardCreateMass):
    """Создание карт"""
    user = await get_user_by_token(token)
    
    inserted_ids = set()

    for loyality_cards_values in loyality_card_data.dict()["__root__"]:
        
        tag_phone = None
        user_by_phone = None
        if loyality_cards_values.get("tags"):
            tags_arr = loyality_cards_values['tags'].split(",")

            for tag in tags_arr:
                if tag.startswith("USERPHONE_"):
                    tag_phone = tag.split("_")[-1]

        if tag_phone:
            q = users.select()
            all_users = await database.fetch_all(q)

            for phone_user in all_users:
                similarity = fuzz.ratio(tag_phone, phone_user.phone_number)
                if similarity >= 80:
                    q = users_cboxes_relation.select().where(users_cboxes_relation.c.cashbox_id == user.cashbox_id, users_cboxes_relation.c.user == phone_user.id, users_cboxes_relation.c.status == True)
                    acc_by_phone = await database.fetch_one(q)
                    if acc_by_phone:
                        user_by_phone = acc_by_phone

        if loyality_cards_values.get("organization_id"):
            loyality_card_org = await get_entity_by_id(organizations, loyality_cards_values["organization_id"], user.id)
            loyality_cards_values["organization_id"] = loyality_card_org.id
        else:
            q = organizations.select().where(organizations.c.owner == user.id, organizations.c.is_deleted == False)
            loyality_card_org = await database.fetch_one(q)
            loyality_cards_values["organization_id"] = loyality_card_org.id

        contr_id = loyality_cards_values.get("contragent_id")
        phone_number = loyality_cards_values.get("phone_number")
        contr_name = loyality_cards_values.get("contragent_name")
        loyality_card_contr = 0

        if contr_id:
            loyality_card_contr = await get_entity_by_id_cashbox(contragents, loyality_cards_values["contragent_id"], user.cashbox_id)
            if not loyality_cards_values["card_number"]:
                if loyality_card_contr.phone:
                    loyality_cards_values["card_number"] = int(loyality_card_contr.phone)
                else:
                    loyality_cards_values["card_number"] = randint(0, 9_223_372_036_854_775)

        else:
            if phone_number:
                q = contragents.select().where(contragents.c.phone == str(phone_number), contragents.c.cashbox == user.cashbox_id)
                loyality_card_contr = await database.fetch_one(q)

                if loyality_card_contr:
                    if loyality_card_contr.phone:
                        loyality_cards_values["card_number"] = int(loyality_card_contr.phone)
                    else:
                        loyality_cards_values["card_number"] = randint(0, 9_223_372_036_854_775)
                else:
                    time = int(datetime.now().timestamp())
                    q = contragents.insert().values(
                        { 
                            "name": contr_name if contr_name else "", 
                            "external_id": "", 
                            "phone": str(phone_number), 
                            "inn": "",
                            "description": "",
                            "cashbox": user.cashbox_id,
                            "is_deleted": False,
                            "created_at": time,
                            "updated_at": time
                        }
                    )
                    new_contr_id = await database.execute(q)
                    q = contragents.select().where(contragents.c.id == new_contr_id)
                    loyality_card_contr = await database.fetch_one(q)
                    loyality_cards_values["card_number"] = int(loyality_card_contr.phone)

            # loyality_cards_db_ex = [card]
            # loyality_cards_db_ex = [*map(add_status, loyality_cards_db_ex)]
            # loyality_cards_db_ex = [*map(datetime_to_timestamp, loyality_cards_db_ex)]

            # return loyality_cards_db_ex
            # while card:
            #     loyality_cards_values["card_number"] = randint(0, 9_223_372_036_854_775)
            #     q = loyality_cards.select().where(loyality_cards.c.card_number == loyality_cards_values["card_number"], loyality_cards.c.cashbox_id == user.cashbox_id)
            #     card = await database.fetch_one(q)


        loyality_cards_values["created_by_id"] = user.id
        if user_by_phone:
            loyality_cards_values["created_by_id"] = user_by_phone.id
        loyality_cards_values["cashbox_id"] = user.cashbox_id

        del loyality_cards_values["phone_number"]
        del loyality_cards_values["contragent_name"]

        loyality_cards_values["contragent_id"] = loyality_card_contr.id

        loyality_cards_values["balance"] = 0
        loyality_cards_values["income"] = 0
        loyality_cards_values["outcome"] = 0

        q = loyality_settings.select().where(
                loyality_settings.c.cashbox == user.cashbox_id,
            )
        setting = await database.fetch_one(q)

        if loyality_cards_values.get("organization_id"):
            q = loyality_settings.select().where(
                loyality_settings.c.cashbox == user.cashbox_id,
                loyality_settings.c.organization == loyality_cards_values.get("organization_id")
            )
            setting_org = await database.fetch_one(q)

            if setting_org:
                setting = setting_org
            

        if setting:
            loyality_cards_values['cashback_percent'] = setting.cashback_percent
            loyality_cards_values['minimal_checque_amount'] = setting.minimal_checque_amount
            loyality_cards_values['max_percentage'] = setting.max_percentage
            loyality_cards_values['max_withdraw_percentage'] = setting.max_withdraw_percentage
            loyality_cards_values['start_period'] = setting.start_period
            loyality_cards_values['end_period'] = setting.end_period

            if not loyality_cards_values['tags']:
                if setting.tags:
                    loyality_cards_values['tags'] = setting.tags
        else:
            for field in ['cashback_percent', 'minimal_checque_amount', 'max_percentage', 'max_withdraw_percentage']:
                if not loyality_cards_values.get(field):
                    loyality_cards_values[field] = 0

            if loyality_cards_values.get("start_period") and loyality_cards_values.get("end_period"):
                loyality_cards_values["start_period"] = datetime.fromtimestamp(loyality_cards_values["start_period"])
                loyality_cards_values["end_period"] = datetime.fromtimestamp(loyality_cards_values["end_period"])

            if loyality_cards_values.get("start_period") and not loyality_cards_values.get("end_period"):
                start_plus_ten = loyality_cards_values["start_period"] + 315576000
                loyality_cards_values["start_period"] = datetime.fromtimestamp(loyality_cards_values["start_period"])
                loyality_cards_values["end_period"] = datetime.fromtimestamp(start_plus_ten)

            if not loyality_cards_values.get("start_period") and not loyality_cards_values.get("end_period"):
                loyality_cards_values["start_period"] = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                start_plus_ten = int(loyality_cards_values["start_period"].timestamp()) + 315576000
                loyality_cards_values["end_period"] = datetime.fromtimestamp(start_plus_ten)

            if not loyality_cards_values.get("start_period") and loyality_cards_values.get("end_period"):
                loyality_cards_values["start_period"] = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
                start_plus_ten = int(loyality_cards_values["start_period"].timestamp()) + 315576000
                loyality_cards_values["end_period"] = datetime.fromtimestamp(start_plus_ten)

        
        q = loyality_cards.select().where(
            or_(
                loyality_cards.c.card_number == loyality_cards_values["card_number"],
                loyality_cards.c.contragent_id == loyality_cards_values["contragent_id"]
            ),
            loyality_cards.c.cashbox_id == user.cashbox_id,
            loyality_cards.c.is_deleted == False
        )
        card = await database.fetch_one(q)

        if card:
            inserted_ids.add(card.id)
        else:
            query = loyality_cards.insert().values(loyality_cards_values)
            loyality_card_id = await database.execute(query)
            inserted_ids.add(loyality_card_id)

    query = (
        loyality_cards.select()
            .where(
            loyality_cards.c.cashbox_id == user.cashbox_id,
            loyality_cards.c.id.in_(inserted_ids)
        )
    )

    loyality_cards_db = await database.fetch_all(query)
    loyality_cards_db = [*map(datetime_to_timestamp, loyality_cards_db)]
    loyality_cards_db = [*map(add_status, loyality_cards_db)]

    socket_instances = [*map(contr_org_ids_to_name, loyality_cards_db)]
    socket_instances = [await instance for instance in socket_instances]

    await manager.send_message(
        token,
        {
            "action": "create",
            "target": "loyality_cards",
            "result": socket_instances,
        },
    )

    return loyality_cards_db


@router.patch("/loyality_cards/{idx}", response_model=schemas.LoyalityCard)
async def edit_loyality_transaction(
    token: str,
    idx: int,
    loyality_card: schemas.LoyalityCardEdit,
):
    """Редактирование карт"""
    user = await get_user_by_token(token)
    loyality_card_db = await get_entity_by_id_and_created_by(loyality_cards, idx, user.id)
    loyality_card_values = loyality_card.dict(exclude_unset=True)

    if loyality_card_values:

        if loyality_card_values.get("max_percentage"):
            if loyality_card_values["max_percentage"] > 100: loyality_card_values["max_percentage"] = 100
        if loyality_card_values.get("max_withdraw_percentage"):
            if loyality_card_values["max_withdraw_percentage"] > 100: loyality_card_values["max_withdraw_percentage"] = 100

        if loyality_card_values.get("start_period"):
            loyality_card_values["start_period"] = datetime.fromtimestamp(loyality_card_values["start_period"])
        if loyality_card_values.get("end_period"):
            loyality_card_values["end_period"] = datetime.fromtimestamp(loyality_card_values["end_period"])

        query = (
            loyality_cards.update()
            .where(loyality_cards.c.id == idx, loyality_cards.c.created_by_id == user.id)
            .values(loyality_card_values)
        )
        await database.execute(query)
        loyality_card_db = await get_entity_by_id_and_created_by(loyality_cards, idx, user.id)

    loyality_card_db = datetime_to_timestamp(loyality_card_db)

    await manager.send_message(
        token,
        {"action": "edit", "target": "loyality_cards", "result": loyality_card_db},
    )

    return {**loyality_card_db,  **{ "data": { "status": "success" } }}


@router.delete("/loyality_cards/{idx}", response_model=schemas.LoyalityCard)
async def delete_loyality_transaction(token: str, idx: int):
    """Удаление карт"""
    user = await get_user_by_token(token)

    await get_entity_by_id_and_created_by(loyality_cards, idx, user.id)

    query = (
        loyality_cards.update()
        .where(loyality_cards.c.id == idx, loyality_cards.c.created_by_id == user.id)
        .values({"is_deleted": True})
    )
    await database.execute(query)

    query = loyality_cards.select().where(
        loyality_cards.c.id == idx, loyality_cards.c.created_by_id == user.id
    )
    loyality_card_db = await database.fetch_one(query)
    loyality_card_db = datetime_to_timestamp(loyality_card_db)

    await manager.send_message(
        token,
        {
            "action": "delete",
            "target": "loyality_cards",
            "result": loyality_card_db,
        },
    )

    # return loyality_card_db
    return {**loyality_card_db,  **{ "data": { "status": "success" } }}